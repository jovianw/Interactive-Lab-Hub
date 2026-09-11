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

# plant clock: the plant sprouts at 6am, is fully grown with a flower by 10pm,
# and wilts overnight. Stem height + leaf count = time of day.
DEMO = True        # True: one full day every DEMO_SECONDS.  False: real time.
DEMO_SECONDS = 12

while True:
    if DEMO:
        # speed up
        hour = 6 + 24 * (time.time() % DEMO_SECONDS) / DEMO_SECONDS
    else:
        now = time.localtime()
        hour = now.tm_hour + now.tm_min / 60
    day = max(0.0, min(1.0, (hour - 6) / 16))
    night = hour >= 22 or hour < 6

    # Draw a black filled box to clear the image.
    draw.rectangle((0, 0, width, height), outline=0, fill=0)

    # pot
    draw.rectangle((100, 110, 140, 134), fill="#B5651D")
    # stem grows from the pot rim toward the top of the screen
    stem_top = 110 - int(90 * day)
    green = "#556B2F" if night else "#2E8B57"
    draw.line((120, 110, 120, stem_top), fill=green, width=4)
    # one leaf per ~20% of the day, alternating sides; leaves droop at night
    for i in range(int(day * 5)):
        y = 110 - 18 * (i + 1) + (6 if night else 0)
        side = 1 if i % 2 else -1
        cx = 120 + side * 20
        draw.ellipse((cx - 10, y - 6, cx + 10, y + 6), fill=green)
    # flower once fully grown
    if day >= 1.0:
        draw.ellipse((110, stem_top - 10, 130, stem_top + 10), fill="#FF69B4")

    # Display image.
    disp.image(image, rotation)
    time.sleep(0.1 if DEMO else 60)
