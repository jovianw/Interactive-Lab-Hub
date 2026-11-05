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


def instruction(task):
    """Describe a physical action without fictional control names or hidden units."""
    kind, value = task['kind'], task['value']
    if kind == 'encoder':
        direction = 'CLOCKWISE' if value > 0 else 'COUNTER CLOCKWISE'
        return f'TURN ENCODER {abs(value)} CLICKS {direction}'
    if kind == 'camera':
        return {'pinch': 'PINCH FINGERS AT CAMERA', 'open': 'OPEN HAND AT CAMERA'}[value]
    if kind == 'distance':
        sensor = 'PROXIMITY SENSOR' if task['control'] == 'proximity' else 'DISTANCE SENSOR'
        return {'near': 'HAND CLOSE TO ', 'far': 'HAND AWAY FROM '}[value] + sensor
    if kind == 'color':
        return f'SHOW {value.upper()} TO COLOR SENSOR'
    if kind == 'joystick':
        return f'PUSH JOYSTICK {value.upper()}'
    if kind == 'touch':
        return f'TOUCH PAD {value}'
    return {1: 'PRESS BUTTON', 2: 'PRESS BUTTON TWICE', 3: 'PRESS BUTTON 3 TIMES'}[task.get('repetitions', 1)]


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
        self.finished = False
        self.notice = 'GET READY'
        self.assign(now=self.start)

    @property
    def task_seconds(self):
        return max(7.5, 20 - .75 * (self.level - 1))

    def next_round(self, now):
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

    def new_task(self, target, caller, now):
        controls = self.players[target]['controls']
        previous = self.tasks.get(target)
        candidates = []
        for control, kind in controls.items():
            options = ['near', 'far'] if kind == 'distance' else CHOICES[kind]
            # Even a station with only a button has two distinct actions.
            repetitions = (1, 2, 3) if kind == 'button' else (1,)
            for value in options:
                for count in repetitions:
                    candidate = dict(control=control, kind=kind, value=value, repetitions=count)
                    if previous is None or instruction(candidate) != instruction(previous):
                        candidates.append(candidate)
        fresh = [t for t in candidates if t['kind'] == 'encoder'
                 or t['value'] != self.readings.get((target, t['control']))]
        # Picks the device first so twelve touch pads don't outweigh one button
        options = fresh or candidates
        control = self.rng.choice(sorted({t['control'] for t in options}))
        selected = self.rng.choice([t for t in options if t['control'] == control])
        control, kind, value = selected['control'], selected['kind'], selected['value']
        current = self.readings.get((target, control))
        # Encoder turns are measured from the first reading sent for this task
        self.tasks[target] = dict(id=uuid.uuid4().hex, caller=caller, target=target,
                                  control=control, kind=kind, value=value,
                                  repetitions=selected['repetitions'], progress=0,
                                  issued=now, deadline=now + self.task_seconds, origin=None,
                                  armed=current is not None and current != value)
        self.matches.pop(target, None)


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
        if not t or now < t['issued']:
            return
        if now >= t['deadline']:
            return
        if control != t['control']:
            kind = self.players[player]['controls'].get(control)
            if kind in ('button', 'touch') and value not in (None, 'released') and value != previous:
                self.penalty('WRONG ACTION -5', 5)
                self.report(player, now)
            return
        if t['kind'] == 'encoder':
            if not isinstance(value, int) or isinstance(value, bool):
                return
            if t['origin'] is None:
                t['origin'] = value
            delta = value - t['origin']
            matched = delta >= t['value'] if t['value'] > 0 else delta <= t['value']
            t['armed'] = True
        else:
            matched = value == t['value']
        if not matched:
            t['armed'] = True
            self.matches.pop(player, None)
            # Only explicit button/touch actions are penalized. Analog travel,
            # uncertain camera detections and color changes are free.
            if t['kind'] in ('button', 'touch') and value not in (None, 'released') and value != previous:
                self.penalty('WRONG ACTION -5', 5)
                self.report(player, now)
            return
        if not t['armed']:
            return
        since = self.matches.setdefault(player, now)
        hold = 0 if t['kind'] in ('button', 'touch', 'encoder') else 0.35
        if now - since >= hold:
            if t['kind'] in ('button', 'touch'):
                t['progress'] += 1
                if t['progress'] < t['repetitions']:
                    t['armed'] = False
                    self.matches.pop(player, None)
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
                    if t['kind'] == 'encoder':
                        t['origin'] = None
                    t['armed'] = self.readings.get((t['target'], t['control'])) != t['value']
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
        return dict(round=self.id, phase='finished' if self.finished else 'playing',
                    remaining=max(0, min(self.duration, self.end - now)), countdown=max(0, self.start - now),
                    score=self.score, completed=self.completed, mistakes=self.mistakes,
                    players=self.players, tasks=self.tasks, asteroid=self.asteroid,
                    level=self.level, round_errors=self.round_errors, error_limit=self.error_limit,
                    task_seconds=self.task_seconds,
                    notice=self.notice, feedback=self.feedback, clock=now)
