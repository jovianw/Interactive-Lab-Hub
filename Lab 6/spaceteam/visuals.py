"""Display-only drawing; nothing here changes the coordinator's task values."""
import math
import random

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 135, 240
SPLIT = 120  # Instruction above, device grid below
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
GREEN, RED = '#19d35a', '#ff2d4d'
# Each round flies through a new place: background and star colors
PLACES = [('#071827', ('#20364f', '#35516d', '#52738b')),
          ('#1b0a2e', ('#3a2160', '#5a3a8a', '#8a6ab8')),
          ('#06241f', ('#145044', '#1f7a66', '#3aa88e')),
          ('#2a1f0a', ('#5a4414', '#8a6a1f', '#c29a3a')),
          ('#2b0a0a', ('#5a1a1a', '#8a2a2a', '#c24a4a'))]
# Round that introduces each challenge: title and what it does
CHALLENGES = {2: ('GLITCH', 'Typos in orders'), 3: ('TURBULENCE', 'Shaky text'),
              4: ('NEBULA', 'Fading text'), 5: ('WORMHOLE', 'Mirrored text')}
HAZARD_COLORS = {'ASTEROIDS': '#5c1025', 'SOLAR FLARE': '#7a3a00',
                 'ION STORM': '#3b1060', 'BLACK HOLE': '#101010'}
fonts = {}


def font(size):
    if size not in fonts:
        fonts[size] = ImageFont.truetype(FONT, size)
    return fonts[size]


def damaged_text(text, level, task_id):
    """Stable glitches per task, preserving numbers and word edges as clues."""
    rng = random.Random(task_id)
    chance = 0 if level < 2 else min(.3, .1 + .04 * (level - 2))
    letters = list(text)
    for i in range(1, len(text) - 1):
        if (text[i - 1:i + 2].isalpha() and rng.random() < chance):
            letters[i] = '_' if rng.random() < .5 else rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
    return ''.join(letters)


def shake_offset(level, now, line):
    if level < 3:
        return 0, 0
    amplitude = min(10, 3 + 2 * (level - 3))
    rng = random.Random(f'{int(now * 10)}:{line}')
    return rng.randint(-amplitude, amplitude), rng.randint(-amplitude, amplitude)


def draw_stars(draw, now, level=1, warp=1):
    """Stars stream downward as the ship flies; warp stretches them between rounds."""
    background, colors = PLACES[(level - 1) % len(PLACES)]
    draw.rectangle((0, 0, WIDTH, SPLIT), fill=background)
    for i in range(70):
        speed = (10 + (i % 5) * 8) * warp
        x = (i * 53 + i * i) % WIDTH
        y = (i * 37 + now * speed) % (SPLIT + 12) - 6
        length = 1 + i % 3 + (warp - 1) * 2
        draw.line((x, y, x, y + length), fill=colors[i % 3])


def draw_hazard(draw, name, now):
    draw.rectangle((0, 0, WIDTH, SPLIT), fill=HAZARD_COLORS[name])
    cx, cy = WIDTH // 2, SPLIT // 2
    if name == 'ASTEROIDS':
        for i in range(8):
            r = 4 + i % 4 * 3
            x, y = (i * 41 + now * 30) % WIDTH, (i * 29 + now * 50) % SPLIT
            draw.ellipse((x - r, y - r, x + r, y + r), fill='#6b6b6b', outline='#9a9a9a')
    elif name == 'SOLAR FLARE':
        for i in range(12):
            angle = math.radians(i * 30 + now * 40)
            draw.line((cx, cy, cx + 150 * math.cos(angle), cy + 150 * math.sin(angle)), fill='#c06a10', width=3)
    elif name == 'ION STORM':
        rng = random.Random(int(now * 6))
        for _ in range(3):
            x, y = rng.randint(0, WIDTH), 0
            points = [(x, y)]
            while y < SPLIT:
                x, y = x + rng.randint(-14, 14), y + rng.randint(10, 22)
                points.append((x, y))
            draw.line(points, fill='#d0a0ff', width=2)
    else:  # BLACK HOLE
        for i in range(6):
            r = 8 + i * 12 + (now * 20) % 12
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline='#8a4aff', width=2)


def draw_ship(draw, progress, now):
    cx = WIDTH // 2
    y = SPLIT + 20 - progress * (SPLIT + 60)  # Flies in from below and off the top
    flame = 10 + random.Random(int(now * 20)).randint(0, 8)
    draw.polygon([(cx - 6, y + 10), (cx + 6, y + 10), (cx, y + 10 + flame)], fill='#ff9a1e')
    draw.polygon([(cx, y - 22), (cx - 12, y + 10), (cx + 12, y + 10)], fill='#d8dde3', outline='white')
    draw.ellipse((cx - 4, y - 8, cx + 4, y), fill='#59e4ff')


def fit(lines, width=WIDTH - 10, size=30):
    """Wraps the lines at the largest font size that fits the top half."""
    while True:
        f = font(size)
        wrapped = []
        for line in lines:
            current = ''
            for word in str(line).split():
                trial = (current + ' ' + word).strip()
                if current and f.getlength(trial) > width:
                    wrapped.append(current)
                    current = word
                else:
                    current = trial
            wrapped.append(current)
        fits = len(wrapped) * size * 1.2 <= SPLIT - 10 and all(f.getlength(w) <= width for w in wrapped)
        # Shrinks before wrapping short titles, but wraps long instructions while still readable
        if size <= 10 or (fits and (len(wrapped) == len(lines) or size <= 18)):
            return f, wrapped
        size -= 2


def draw_text(image, draw, lines, now, level, effects):
    """The round's challenges only apply to task text (effects), never to menus."""
    f, wrapped = fit(lines)
    step = round(f.size * 1.2)
    y = (SPLIT - step * len(wrapped)) // 2 + step // 2
    fill = 'white'
    if effects and level >= 4:  # Nebula: text fades in and out
        glow = .3 + .7 * abs(math.sin(now * 1.3))
        fill = (round(255 * glow),) * 3
    for index, line in enumerate(wrapped):
        dx, dy = shake_offset(level, now, index) if effects else (0, 0)
        draw.text((WIDTH // 2 + dx + 1, y + dy + 1), line, font=f, fill='black', anchor='mm')
        draw.text((WIDTH // 2 + dx, y + dy), line, font=f, fill=fill, anchor='mm')
        y += step
    if effects and level >= 5:  # Wormhole: mirrored text
        image.paste(ImageOps.mirror(image.crop((0, 0, WIDTH, SPLIT))), (0, 0))


def draw_cell(draw, box, kind, value, last):
    x0, y0, x1, y1 = box
    active = value not in (None, 'released', 'neutral')
    touched = kind == 'touch' and active
    draw.rectangle(box, fill='white' if touched else None, outline='#35516d')
    ink = 'black' if touched else 'white'
    draw.text((x0 + 4, y0 + 2), kind.upper(), font=font(9 if x1 - x0 >= 60 else 7),
              fill='black' if touched else '#59e4ff')
    cx, cy = (x0 + x1) // 2, (y0 + y1 + 10) // 2
    r = min(x1 - x0, y1 - y0 - 10) // 2 - 6
    if kind == 'button':
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline='white', width=2,
                     fill='white' if value == 'press' else None)
    elif kind == 'touch':
        draw.text((cx, cy), '-' if last is None else str(last), font=font(min(2 * r, 30)), fill=ink, anchor='mm')
    elif kind == 'encoder':
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline='white', width=2)
        if value is not None:
            angle = math.radians(value * 15 - 90)  # 24 clicks per turn, starting at 12 o'clock
            draw.line((cx, cy, cx + r * math.cos(angle), cy + r * math.sin(angle)), fill='#ffdd78', width=3)
    elif kind == 'joystick':
        draw.rectangle((cx - r, cy - r, cx + r, cy + r), outline='white')
        dx, dy = {'up': (0, -1), 'down': (0, 1), 'left': (-1, 0), 'right': (1, 0)}.get(value, (0, 0))
        px, py = cx + dx * (r - 6), cy + dy * (r - 6)
        draw.ellipse((px - 5, py - 5, px + 5, py + 5), fill='#ffdd78' if active else 'white')
    elif kind == 'color':
        draw.rectangle((cx - r, cy - r, cx + r, cy + r), outline='white', fill=value if active else None)
    elif kind == 'distance':
        # Bar fills from the sensor at the bottom up to the hand
        level = {'near': 1, 'middle': 2, 'far': 3}.get(value, 0)
        for i in range(3):
            top = cy + r - (i + 1) * (2 * r // 3) + 2
            draw.rectangle((cx - r, top, cx + r, top + 2 * r // 3 - 3), outline='white',
                           fill='#ffdd78' if i < level else None)
    elif kind == 'camera':
        # Open hand fans out from the palm; pinch brings the fingertips together
        draw.ellipse((cx - r // 2, cy + r // 3, cx + r // 2, cy + r), outline='white', width=2)
        for i in range(5):
            spread = (i - 2) * (0.55 if value == 'open' else 0.15)
            tip = (cx + r * math.sin(spread), cy + r // 3 - r * math.cos(spread) * (1 if value else 0.5))
            draw.line((cx, cy + r // 3, *tip), fill='#ffdd78' if value else 'white', width=2)


def screen(lines, cells, now, level=1, border=None, hazard=None, warp=1, effects=False, ship=None):
    """Text centered over the stars, then one cell per device showing its state."""
    image = Image.new('RGB', (WIDTH, HEIGHT), '#071827')
    draw = ImageDraw.Draw(image)
    if hazard:
        draw_hazard(draw, hazard, now)
    else:
        draw_stars(draw, now, level, warp)
    if ship is not None:
        draw_ship(draw, ship, now)
    draw_text(image, draw, lines, now, level, effects)
    draw.rectangle((0, SPLIT, WIDTH, HEIGHT), fill='#071827')
    columns = 1 if len(cells) == 1 else 2 if len(cells) <= 4 else 3
    rows = max(1, math.ceil(len(cells) / columns))
    cw, ch = WIDTH // columns, (HEIGHT - SPLIT) // rows
    for index, (kind, value, last) in enumerate(cells):
        x0, y0 = (index % columns) * cw, SPLIT + (index // columns) * ch
        draw_cell(draw, (x0, y0, x0 + cw - 1, y0 + ch - 1), kind, value, last)
    if border:
        for i in range(5):
            draw.rectangle((i, i, WIDTH - 1 - i, HEIGHT - 1 - i), outline=border)
    return image
