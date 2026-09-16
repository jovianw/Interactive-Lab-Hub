import time
import subprocess
import digitalio
import board
from PIL import Image, ImageDraw, ImageFont
import adafruit_rgb_display.st7789 as st7789

# Configuration for CS and DC pins (these are FeatherWing defaults on M0/M4):
cs_pin = digitalio.DigitalInOut(board.D5) 
dc_pin = digitalio.DigitalInOut(board.D25)
reset_pin = None

# Config for display baudrate (default max is 24mhz):
BAUDRATE = 64000000

# Setup SPI bus using hardware SPI:
spi = board.SPI()

# Create the ST7789 display:
disp = st7789.ST7789(
    spi,
    cs=cs_pin,
    dc=dc_pin,
    rst=reset_pin,
    baudrate=BAUDRATE,
    width=135,
    height=240,
    x_offset=53,
    y_offset=40,
)

# Create blank image for drawing.
# Make sure to create image with mode 'RGB' for full color.
height = disp.width  # we swap height/width to rotate it to landscape!
width = disp.height
image = Image.new("RGB", (width, height))
rotation = 90

# Get drawing object to draw on image.
draw = ImageDraw.Draw(image)

# Draw a black filled box to clear the image.
draw.rectangle((0, 0, width, height), outline=0, fill=(0, 0, 0))
disp.image(image, rotation)
# Draw some shapes.
# First define some constants to allow easy resizing of shapes.
padding = -2
top = padding
bottom = height - padding
# Move left to right keeping track of the current x position for drawing shapes.
x = 0

# Alternatively load a TTF font.  Make sure the .ttf font file is in the
# same directory as the python script!
# Some other nice fonts to try: http://www.dafont.com/bitmap.php
font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)

# Turn on the backlight
backlight = digitalio.DigitalInOut(board.D22)
backlight.switch_to_output()
backlight.value = True

# plant clock: grows one leaf per hour from midnight to noon (12 leaves, 6 per
# side), flower opens 10am-noon, then it wilts and loses a leaf per hour back to
# nothing at midnight. Leaves = hours from midnight (morning) or to midnight
# (evening), rounded to the nearest hour.
DEMO = True        # True: one full day every DEMO_SECONDS.  False: real time.
DEMO_SECONDS = 12

def mix(a, b, t):
    # blend two RGB tuples, t = 0 (all a) .. 1 (all b)
    return tuple(int(x + (y - x) * t) for x, y in zip(a, b))

while True:
    if DEMO:
        # speed up
        hour = 24 * (time.time() % DEMO_SECONDS) / DEMO_SECONDS
    else:
        now = time.localtime()
        hour = now.tm_hour + now.tm_min / 60
    size = 12 - abs(hour - 12)                   # 0 at midnight, 12 at noon, 0 at midnight
    leaves = int(size + 0.5)                     # one leaf per hour, rounded to the nearest hour
    bloom = max(0.0, min(1.0, (hour - 10) / 2))  # flower opens 10am -> noon
    wilt = max(0.0, min(1.0, (hour - 12) / 12))  # noon -> midnight

    green = mix((46, 139, 87), (85, 107, 47), wilt)
    pink = mix((255, 105, 180), (139, 90, 60), wilt)

    # Draw a black filled box to clear the image.
    draw.rectangle((0, 0, width, height), outline=0, fill=0)

    # pot
    draw.rectangle((100, 110, 140, 134), fill="#B5651D")
    # stem: 8px per hour from the pot rim, smoothly up in the morning, down after noon
    stem_top = 110 - int(8 * size)
    draw.line((120, 110, 120, stem_top), fill=green, width=4)
    # one leaf per hour, alternating sides (6 per side at noon), touching the
    # stem; they droop as the plant wilts. Leaf n sits half a row below the
    # stem tip when it appears at n-0.5 hours, so it's always attached.
    for i in range(leaves):
        y = 110 - 8 * (i + 1) + 4 + int(6 * wilt)
        if i % 2:
            draw.ellipse((122, y - 4, 142, y + 4), fill=green)  # right leaf
        else:
            draw.ellipse((98, y - 4, 118, y + 4), fill=green)   # left leaf
    # flower on the stem tip: opens with bloom, shrinks and sags with wilt
    r = int(10 * min(bloom, 1 - wilt))
    if r > 0:
        fx = 120 + int(3 * wilt)
        fy = stem_top + int(6 * wilt)
        draw.ellipse((fx - r, fy - r, fx + r, fy + r), fill=pink)

    # Display image.
    disp.image(image, rotation)
    time.sleep(0.1 if DEMO else 60)
