"""Hardware-independent game rules. All durations use the coordinator's clock."""
import random
import uuid

CHOICES = {
    'camera': ['pinch', 'open'],
    'joystick': ['up', 'down', 'left', 'right'],
    'button': ['press'], 'touch': [str(pad) for pad in range(12)],
    'encoder': [-5, -4, -3, -2, 2, 3, 4, 5],
    'distance': ['near', 'middle', 'far'],
    'color': ['red', 'green', 'blue'],
}
ROUNDS = 10  # Survive this many to win
COMBO_ROUND = 6  # From here on, some tasks need two inputs at once
FAST_ROUND = 8  # From here on, task time is halved


def describe(part):
    """Describe a physical action without fictional control names or hidden units."""
    kind, value = part['kind'], part['value']
    if kind == 'encoder':
        direction = 'CLOCKWISE' if value > 0 else 'COUNTER CLOCKWISE'
        return f'TURN ENCODER {abs(value)} CLICKS {direction}'
    if kind == 'camera':
        return {'pinch': 'PINCH FINGERS AT CAMERA', 'open': 'OPEN HAND AT CAMERA'}[value]
    if kind == 'distance':
        sensor = 'PROXIMITY SENSOR' if part['control'] == 'proximity' else 'DISTANCE SENSOR'
        return {'near': 'HAND CLOSE TO ', 'far': 'HAND AWAY FROM '}[value] + sensor
    if kind == 'color':
        return f'SHOW {value.upper()} TO COLOR SENSOR'
    if kind == 'joystick':
        return f'PUSH JOYSTICK {value.upper()}'
    if kind == 'touch':
        return f'TOUCH PAD {value}'
    return {1: 'PRESS BUTTON', 2: 'PRESS BUTTON TWICE', 3: 'PRESS BUTTON 3 TIMES'}[part.get('repetitions', 1)]


def instruction(task):
    return ' AND '.join(describe(part) for part in task['parts'])


# Same alternate-A/B event, different name and background each time
HAZARDS = ['ASTEROIDS', 'SOLAR FLARE', 'ION STORM', 'BLACK HOLE']


class Game:
    def __init__(self, players, now, rng=None, duration=60):
        self.rng = rng or random.Random()
        self.players = players
        self.id = uuid.uuid4().hex
        self.level = 1
        self.duration = duration
        self.error_limit = 5
        self.round_errors = 0
        self.start = now + 7  # Launch animation, then the countdown
        self.end = self.start + duration
        self.score = self.completed = self.mistakes = 0
        self.tasks = {}
        self.readings = {}
        self.matches = {}
        self.feedback = {}
        self.feedback_seq = 0
        self.asteroid = None
        self.asteroid_index = 0
        self.finished = self.won = False
        self.notice = 'GET READY'
        self.assign(now=self.start)

    @property
    def task_seconds(self):
        seconds = max(7.5, 20 - .75 * (self.level - 1))
        return seconds / 2 if self.level >= FAST_ROUND else seconds

    def next_round(self, now):
        if self.level >= ROUNDS:
            self.finished = self.won = True
            self.notice = 'MISSION COMPLETE'
            return
        self.level += 1
        self.round_errors = 0
        self.start = now + 4
        self.end = self.start + self.duration
        self.asteroid = None
        self.asteroid_index = 0
        self.matches.clear()
        self.feedback.clear()
        self.notice = 'ROUND CLEAR'
        for player, task in list(self.tasks.items()):
            self.new_task(player, task['caller'], self.start)

    def assign(self, now):
        # A shuffled cycle is a derangement: one caller and target per player.
        ids = list(self.players)
        self.rng.shuffle(ids)
        for i, target in enumerate(ids):
            caller = ids[(i + 1) % len(ids)]
            self.new_task(target, caller, now)

    def new_part(self, target, avoid_text=None, avoid_control=None):
        candidates = []
        for control, kind in self.players[target]['controls'].items():
            if control == avoid_control:
                continue
            options = ['near', 'far'] if kind == 'distance' else CHOICES[kind]
            # Even a station with only a button has distinct actions.
            repetitions = (1, 2, 3) if kind == 'button' else (1,)
            for value in options:
                for count in repetitions:
                    candidate = dict(control=control, kind=kind, value=value, repetitions=count)
                    if describe(candidate) != avoid_text:
                        candidates.append(candidate)
        fresh = [c for c in candidates if c['kind'] == 'encoder'
                 or c['value'] != self.readings.get((target, c['control']))]
        # Picks the device first so twelve touch pads don't outweigh one button
        options = fresh or candidates
        control = self.rng.choice(sorted({c['control'] for c in options}))
        part = self.rng.choice([c for c in options if c['control'] == control])
        current = self.readings.get((target, control))
        # Encoder turns are measured from the first reading sent for this task
        part.update(progress=0, origin=None, done=None, armed=current is not None and current != part['value'])
        return part

    def new_task(self, target, caller, now):
        previous = self.tasks.get(target)
        parts = [self.new_part(target, avoid_text=previous and describe(previous['parts'][0]))]
        # Both hands: rare at first, then most tasks
        chance = min(.8, .2 * (self.level - COMBO_ROUND + 1))
        if len(self.players[target]['controls']) > 1 and self.rng.random() < chance:
            parts.append(self.new_part(target, avoid_control=parts[0]['control']))
        seconds = self.task_seconds * (1.5 if len(parts) > 1 else 1)
        self.tasks[target] = dict(id=uuid.uuid4().hex, caller=caller, target=target, parts=parts,
                                  issued=now, deadline=now + seconds)
        self.matches = {k: v for k, v in self.matches.items() if k[0] != target}

    def report(self, target, now, success=False):
        # Shown to the doer and the caller; target None means the whole crew
        self.feedback_seq += 1
        recipients = self.players if target is None else (target, self.tasks[target]['caller'])
        for player in recipients:
            self.feedback[player] = dict(id=self.feedback_seq, at=now, success=success, target=target)

    def input(self, player, control, value, now):
        previous = self.readings.get((player, control))
        self.readings[player, control] = value
        if self.finished or now < self.start or now >= self.end or self.asteroid:
            return
        t = self.tasks.get(player)
        if not t or now < t['issued'] or now >= t['deadline']:
            return
        # Only explicit button/touch actions are penalized. Analog travel,
        # uncertain camera detections and color changes are free.
        pressed = value not in (None, 'released') and value != previous
        part = next((p for p in t['parts'] if p['control'] == control), None)
        if part is None:
            if self.players[player]['controls'].get(control) in ('button', 'touch') and pressed:
                self.penalty('WRONG ACTION -5', 5)
                self.report(player, now)
            return
        if part['kind'] == 'encoder':
            if not isinstance(value, int) or isinstance(value, bool):
                return
            if part['origin'] is None:
                part['origin'] = value
            delta = value - part['origin']
            matched = delta >= part['value'] if part['value'] > 0 else delta <= part['value']
            part['armed'] = True
        else:
            matched = value == part['value']
        if not matched:
            part['armed'] = True
            self.matches.pop((player, control), None)
            if part['kind'] in ('button', 'touch') and pressed:
                self.penalty('WRONG ACTION -5', 5)
                self.report(player, now)
            return
        if not part['armed']:
            return
        since = self.matches.setdefault((player, control), now)
        hold = 0 if part['kind'] in ('button', 'touch', 'encoder') else 0.35
        if now - since < hold:
            return
        if part['kind'] in ('button', 'touch'):
            part['progress'] += 1
            part['armed'] = False
            self.matches.pop((player, control), None)
            if part['progress'] < part['repetitions']:
                return
        part['done'] = now
        done = [p['done'] for p in t['parts']]
        if None in done:
            return
        if max(done) - min(done) > 1.5:
            # Not at the same time: the earlier input must be redone
            for p in t['parts']:
                if p['done'] < max(done):
                    p.update(done=None, progress=0, origin=None)
            return
        self.score += max(20, round(100 - 8 * (now - t['issued'])))
        self.completed += 1
        self.notice = 'TASK COMPLETE!'
        self.report(player, now, success=True)
        self.new_task(player, t['caller'], now)

    def action(self, player, button, now):
        a = self.asteroid
        if not a or now < a['action_start'] or now >= a['end'] or player not in self.players:
            return
        progress = a['progress'][player]
        if button in ('A', 'B') and button != progress['last']:
            progress['count'] += 1
            progress['last'] = button

    def penalty(self, message, points):
        if self.finished:
            return
        self.score = max(0, self.score - points)
        self.mistakes += 1
        self.round_errors += 1
        self.notice = message
        if self.round_errors >= self.error_limit:
            self.finished = True
            self.asteroid = None
            self.notice = 'SHIP LOST'

    def expire_tasks(self, now):
        for player, task in list(self.tasks.items()):
            if self.finished:
                return
            if task['deadline'] <= min(now, self.end):
                self.penalty('TASK EXPIRED -15', 15)
                self.report(player, now)
                if not self.finished:
                    self.new_task(player, task['caller'], now)

    def tick(self, now):
        if self.finished or now < self.start:
            return
        if now >= self.end:
            self.expire_tasks(now)
            if not self.finished:
                self.next_round(now)
            return
        if self.asteroid:
            a = self.asteroid
            if now >= a['end']:
                if all(p['count'] >= 6 for p in a['progress'].values()):
                    self.score += 100
                    self.notice = 'ASTEROIDS DODGED! +100'
                    self.report(None, now, success=True)
                else:
                    self.penalty('ASTEROID HIT! -50', 50)
                    self.report(None, now)
                pause = a['end'] - a['started']
                for t in self.tasks.values():
                    t['issued'] += pause
                    t['deadline'] += pause
                    for p in t['parts']:
                        p.update(origin=None, done=None,
                                 armed=self.readings.get((t['target'], p['control'])) != p['value'])
                self.matches.clear()
                self.asteroid = None
            return
        self.expire_tasks(now)
        if self.finished:
            return
        if self.asteroid_index < 2 and now >= self.start + (18, 39)[self.asteroid_index]:
            self.asteroid_index += 1
            self.asteroid = dict(name=self.rng.choice(HAZARDS), started=now, action_start=now + 1.5, end=now + 3.5,
                                 progress={p: {'count': 0, 'last': None} for p in self.players})
            self.matches.clear()
            return

    def snapshot(self, now):
        return dict(round=self.id, phase='finished' if self.finished else 'playing', won=self.won,
                    remaining=max(0, min(self.duration, self.end - now)), countdown=max(0, self.start - now),
                    score=self.score, completed=self.completed, mistakes=self.mistakes,
                    players=self.players, tasks=self.tasks, asteroid=self.asteroid,
                    level=self.level, round_errors=self.round_errors, error_limit=self.error_limit,
                    task_seconds=self.task_seconds,
                    notice=self.notice, feedback=self.feedback, clock=now)
