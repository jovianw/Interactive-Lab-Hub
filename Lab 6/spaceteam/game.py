#!/usr/bin/env python3
"""Run this same command on every Pi. No separately configured server required."""
import argparse
import json
import logging
import math
import os
import queue
import select
import socket
import subprocess
import sys
import time
import uuid

from engine import CHOICES, Game, instruction
from hardware import Display, Hardware
from visuals import CHALLENGES, GREEN, RED, damaged_text

log = logging.getLogger(__name__)
SOUNDS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds')


def play(name):
    subprocess.Popen(['paplay', os.path.join(SOUNDS, f'{name}.wav')])


class Node:
    def __init__(self, args, hardware, display):
        import paho.mqtt.client as mqtt
        self.args, self.hardware, self.display = args, hardware, display
        self.id = args.name or f'{socket.gethostname()[:8]}-{uuid.getnode() & 0xffff:04x}'
        self.boot = uuid.uuid4().hex
        self.root = f'IDD/{args.room}/spaceteam/v1'
        self.inbox = queue.Queue(maxsize=2000)
        self.peers = {}
        self.game = self.state = None
        self.leader = None
        self.ready = False
        self.connected = False
        self.seq = 0
        self.sequences = {}
        self.started = time.monotonic()
        self.state_received = 0
        self.last_round = None
        self.last_heartbeat = self.last_state = self.last_input = 0
        self.last_phase = self.last_feedback = self.last_start = None
        self.last_asteroid = self.last_dodge = None
        self.last_level = (None, None)
        self.values = {k: None for k in hardware.controls}
        self.last_touch = {}
        self.sim_values = dict(self.values)
        self.sim_release = {}
        self.client = mqtt.Client(client_id=f'spaceteam-{self.id}-{self.boot[:8]}')
        self.client.username_pw_set(args.username, args.password)
        self.client.will_set(f'{self.root}/presence/{self.id}', json.dumps({'online': False}), qos=1)
        self.client.reconnect_delay_set(1, 8)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        self.connected = rc == 0
        if self.connected:
            client.subscribe(f'{self.root}/#', qos=1)
            log.info('Connected to %s; room=%s node=%s', self.args.broker, self.args.room, self.id)
        else:
            log.error('MQTT rejected connection: %s', rc)

    def on_disconnect(self, client, userdata, rc):
        self.connected = False

    def on_message(self, client, userdata, message):
        try:
            if len(message.payload) > 100000:
                return
            data = json.loads(message.payload)
            if not isinstance(data, dict):
                return
            self.inbox.put_nowait((message.topic[len(self.root) + 1:], data))
        except (ValueError, queue.Full):
            log.warning('Ignored malformed message or full inbox')

    def publish(self, suffix, data, qos=0):
        self.client.publish(f'{self.root}/{suffix}', json.dumps(data), qos=qos)

    def heartbeat(self, now):
        data = dict(online=True, boot=self.boot, name=self.id,
                    controls=self.hardware.controls, ready=self.ready,
                    playing=bool(self.leader == self.id and self.game
                                 and not self.game.finished))
        self.peers[self.id] = (now, data)
        self.publish(f'presence/{self.id}', data)

    def receive(self, now):
        for _ in range(2000):
            try:
                topic, data = self.inbox.get_nowait()
            except queue.Empty:
                break
            parts = topic.split('/')
            if len(parts) == 2 and parts[0] == 'presence':
                who = parts[1]
                if data.get('online') is False:
                    self.peers.pop(who, None)
                elif (isinstance(data.get('controls'), dict)
                      and all(isinstance(k, str) and isinstance(v, str) and v in CHOICES
                              for k, v in data['controls'].items())
                      and isinstance(data.get('boot'), str)):
                    self.peers[who] = (now, data)
            elif topic == 'state' and data.get('leader') == self.leader:
                peer = self.peers.get(self.leader)
                if peer and data.get('boot') == peer[1]['boot']:
                    self.state, self.state_received = data, now
            elif len(parts) == 2 and parts[0] == 'input' and self.leader == self.id:
                self.process_input(parts[1], data, now)

    def process_input(self, who, data, now):
        game = self.game
        peer = self.peers.get(who)
        if not game or game.finished or who not in game.players or not peer:
            return
        seq = data.get('seq')
        key = (who, peer[1]['boot'])
        if (data.get('boot') != peer[1]['boot'] or not isinstance(seq, int)
                or seq <= self.sequences.get(key, -1) or data.get('round') != game.id):
            return
        self.sequences[key] = seq
        if data.get('button') in ('A', 'B'):
            game.action(who, data['button'], now)
            return
        task = game.tasks.get(who)
        if not task or data.get('task') != task['id']:
            return
        values = data.get('values')
        if not isinstance(values, dict):
            return
        for control, value in values.items():
            # A completed task replaces this object. Remaining readings belong
            # to the old instruction, even if they match the new one.
            if game.tasks.get(who) is not task or game.finished:
                break
            kind = game.players[who]['controls'].get(control)
            if kind and (value is None or (kind == 'encoder' and isinstance(value, int)
                                          and not isinstance(value, bool))
                         or value in CHOICES[kind] or value in ('neutral', 'released')):
                game.input(who, control, value, now)

    def elect(self, now):
        self.peers = {p: v for p, v in self.peers.items() if now - v[0] < 5}
        eligible = sorted(p for p, (_, d) in self.peers.items() if d['controls'])
        active = [p for p in eligible if self.peers[p][1].get('playing')]
        # Include local state before the next heartbeat advertises the run.
        if (self.leader in eligible and self.state
                and self.state.get('phase') == 'playing'
                and self.leader not in active):
            active.append(self.leader)
        leader = min(active) if active else (eligible[0] if eligible else None)
        if leader != self.leader:
            log.info('Coordinator: %s', leader)
            self.leader = leader
            self.game = self.state = None
            self.ready = False
            self.sequences.clear()

    def coordinate(self, now):
        if self.leader != self.id:
            return
        players = {p: d for p, (_, d) in self.peers.items() if d['controls']}
        # A reboot or loss of a participant invalidates a round; late joiners wait.
        if self.game and not self.game.finished:
            if any(p not in players or players[p]['boot'] != d['boot']
                   for p, d in self.game.players.items()):
                self.game = None
                self.ready = False
                log.warning('Participant disconnected; return to lobby')
        # A won game shows its animation, then everyone is sent back to the lobby
        if self.game and self.game.won and now - self.game.end > 10:
            self.game = None
            self.ready = False
        if (not self.game or self.game.finished and not self.game.won) and now - self.started > 5:
            if len(players) >= self.args.min_players and all(d.get('ready') for d in players.values()):
                self.game = Game(players, now)
                self.heartbeat(now)
        if self.game:
            self.game.tick(now)
            state = self.game.snapshot(now)
        else:
            state = dict(phase='lobby', players=players, clock=now)
        state.update(leader=self.id, boot=self.boot)
        self.state, self.state_received = state, now
        if now - self.last_state >= .1:
            self.publish('state', state)
            self.last_state = now

    def send_input(self, button=None):
        s = self.state or {}
        task = s.get('tasks', {}).get(self.id, {})
        self.seq += 1
        data = dict(boot=self.boot, seq=self.seq, round=s.get('round'),
                    task=task.get('id'), values=self.values)
        if button:
            data['button'] = button
        # Reliable edges, periodic readings can be lossy.
        self.publish(f'input/{self.id}', data, qos=1 if button else 0)

    def buttons(self, edges):
        for button in edges:
            s = self.state or {}
            if s.get('phase') in (None, 'lobby', 'finished'):
                if button == 'A':
                    self.ready = not self.ready
                    play('ready')
                    self.heartbeat(time.monotonic())
            else:
                # Plays right away for presses the coordinator will count
                a = s.get('asteroid')
                if a and self.id in a['progress'] and button != self.last_dodge:
                    clock = s['clock'] + time.monotonic() - self.state_received
                    if a['action_start'] <= clock < a['end'] and a['progress'][self.id]['count'] < 6:
                        self.last_dodge = button
                        play('dodge_press')
                self.send_input(button)

    def sounds(self):
        s = self.state or {}
        phase, level = s.get('phase'), (s.get('round'), s.get('level'))
        feedback = s.get('feedback', {}).get(self.id)
        heard = (s['round'], feedback['id']) if feedback else None
        # Plays only game over when the ship is lost
        if phase == 'finished' and self.last_phase == 'playing':
            play('victory' if s['won'] else 'game_over')
        elif phase == 'playing':
            if level[0] == self.last_level[0] and level[1] > self.last_level[1]:
                play('round_clear')
            # Plays at 3s left so later rounds finish the round clear sound first
            if 0 < s['countdown'] <= 3 and level != self.last_start:
                self.last_start = level
                play('round_start')
            if s['asteroid'] and s['asteroid']['started'] != self.last_asteroid:
                self.last_asteroid = s['asteroid']['started']
                self.last_dodge = None
                play('asteroid_alarm')
            if feedback and heard != self.last_feedback:
                if feedback['target'] is None:
                    play('dodged' if feedback['success'] else 'asteroid_hit')
                elif not feedback['success']:
                    play('fail')
                # Plays success only for the player who did the task
                elif feedback['target'] == self.id:
                    play('success')
        self.last_phase, self.last_level, self.last_feedback = phase, level, heard

    def keyboard(self, now):
        if not self.args.simulate or not select.select([sys.stdin], [], [], 0)[0]:
            return
        line = sys.stdin.readline()
        if not line:
            return
        parts = line.strip().lower().split()
        if parts == ['ready']:
            self.buttons(['A'])
        elif len(parts) == 1 and parts[0] in ('a', 'b'):
            self.buttons([parts[0].upper()])
        elif len(parts) == 2 and parts[0] in self.sim_values:
            name, value = parts
            kind = self.hardware.controls[name]
            if kind == 'encoder':
                try:
                    value = int(value)
                except ValueError:
                    return
            if (kind == 'encoder' and isinstance(value, int)
                    or value in CHOICES[kind] or value in ('released', 'neutral', 'unknown')):
                self.sim_values[name] = None if value == 'unknown' else value
                if kind in ('button', 'touch'):
                    self.sim_release[name] = now + .3

    def render(self, now):
        s = self.state
        cells = [(kind, self.values.get(name), self.last_touch.get(name))
                 for name, kind in self.hardware.controls.items()]
        if not self.connected:
            self.display.show(['CONNECTING'], cells, now)
            return
        if not s or now - self.state_received > 3:
            self.display.show(['FINDING CREW'], cells, now)
            return
        phase = s['phase']
        clock = s['clock'] + now - self.state_received
        feedback = s.get('feedback', {}).get(self.id)
        border = None
        if feedback and 0 <= clock - feedback['at'] < 1:
            border = GREEN if feedback['success'] else RED
        if phase == 'lobby':
            ready = sum(bool(p.get('ready')) for p in s['players'].values())
            self.display.show(['READY', f"{ready} / {len(s['players'])}"], cells, now)
            return
        if phase == 'finished' and s['won']:
            self.display.show(['MISSION', 'COMPLETE', f"SCORE {s['score']}"], cells, now,
                              level=int(now * 2), warp=4, ship=(now % 2.5) / 2.5)
            return
        if phase == 'finished':
            self.display.show(['SHIP LOST', f"SCORE {s['score']}", f"ROUND {s['level']}"], cells, now)
            return
        countdown = s['countdown'] - (now - self.state_received)
        if countdown > 0:
            level = s['level']
            if level == 1 and countdown > 3:
                # Launch animation, then the round 1 countdown
                self.display.show(['LAUNCHING'], cells, now, warp=6, ship=min(1, (7 - countdown) / 3))
                return
            title, what = CHALLENGES.get(level, (f'ROUND {level}', ''))
            lines = [title] + ([what] if what else []) + [str(math.ceil(countdown))]
            self.display.show(lines, cells, now, level=level, warp=5)
            return
        if self.id not in s['players']:
            self.display.show(['NEXT ROUND'], cells, now)
            return
        a = s.get('asteroid')
        if a:
            count = min(6, a['progress'][self.id]['count'])
            self.display.show([f"{a['name']}!", 'A / B' if clock >= a['action_start'] else 'READY...',
                               f'{count} / 6'], cells, now, hazard=a['name'])
            return
        task = next(t for t in s['tasks'].values() if t['caller'] == self.id)
        text = damaged_text(instruction(task), s['level'], task['id'])
        self.display.show([text], cells, now, level=s['level'], border=border, effects=True)

    def run(self):
        self.client.connect_async(self.args.broker, self.args.port, keepalive=10)
        self.client.loop_start()
        last_render = 0
        try:
            while True:
                now = time.monotonic()
                self.receive(now)
                if self.connected:
                    if now - self.last_heartbeat >= 1:
                        self.heartbeat(now)
                        self.last_heartbeat = now
                    self.elect(now)
                    self.coordinate(now)
                    self.sounds()
                    s = self.state or {}
                    if s.get('phase') == 'playing' and s.get('round') != self.last_round:
                        self.last_round = s['round']
                        self.ready = False
                        self.heartbeat(now)
                    self.keyboard(now)
                    self.buttons(self.display.edges())
                    if now - self.last_input >= .08:
                        if self.args.simulate:
                            for name, deadline in list(self.sim_release.items()):
                                if now >= deadline:
                                    self.sim_values[name] = 'released'
                                    del self.sim_release[name]
                            self.values = dict(self.sim_values)
                        else:
                            self.values = self.hardware.poll()
                        for name, value in self.values.items():
                            if self.hardware.controls[name] == 'touch' and value not in (None, 'released'):
                                self.last_touch[name] = value
                        self.send_input()
                        self.last_input = now
                if now - last_render >= .15:
                    self.render(now)
                    last_render = now
                time.sleep(.015)
        except KeyboardInterrupt:
            pass
        finally:
            if self.connected:
                self.client.publish(f'{self.root}/presence/{self.id}', json.dumps({'online': False}), qos=1).wait_for_publish(timeout=1)
            self.client.disconnect()
            self.client.loop_stop()
            self.hardware.close()
            self.display.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--room', default=os.getenv('SPACETEAM_ROOM', 'jovian21'))
    parser.add_argument('--name', help='Short, unique station name; defaults to hostname + hardware ID')
    parser.add_argument('--broker', default=os.getenv('MQTT_BROKER', 'farlab.infosci.cornell.edu'))
    parser.add_argument('--port', type=int, default=1883)
    parser.add_argument('--username', default=os.getenv('MQTT_USERNAME', 'idd'))
    parser.add_argument('--password', default=os.getenv('MQTT_PASSWORD', 'device@theFarm'))
    parser.add_argument('--min-players', type=int, default=2)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--no-camera', action='store_true')
    parser.add_argument('--camera-device', default='auto',
                        help='USB webcam index or /dev/videoN path; defaults to automatic detection')
    parser.add_argument('--proximity-near', type=int, default=100)
    parser.add_argument('--proximity-far', type=int, default=20)
    parser.add_argument('--simulate', help='Comma-separated controls: camera,joystick,button,touch,encoder,distance,color')
    args = parser.parse_args()
    if (not args.room or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.room)
            or args.name and (len(args.name) > 16 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.name))):
        parser.error('Use letters, numbers, _ or - for room/name; names at most 16 characters')
    if args.min_players < 2:
        parser.error('At least two players are required')
    if args.proximity_far >= args.proximity_near:
        parser.error('Proximity far threshold must be below near threshold')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if args.simulate:
        kinds = args.simulate.split(',')
        if any(k not in CHOICES for k in kinds):
            parser.error('Unknown simulation control')
        class Simulated:
            controls = {('red_button' if k == 'button' else k): k for k in kinds}
            def close(self):
                pass
        hardware = Simulated()
        print('Commands (press Enter): ready | a | b | encoder 7 | camera pinch | red_button press | touch 3')
    else:
        hardware = Hardware(not args.no_camera, args.proximity_near, args.proximity_far, args.camera_device)
    display = Display(args.headless)
    if not hardware.controls:
        log.warning('No usable sensors detected. Check drivers, wiring and I2C; this node cannot ready up.')
    Node(args, hardware, display).run()


if __name__ == '__main__':
    main()
