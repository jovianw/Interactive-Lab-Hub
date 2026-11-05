"""Optional hardware adapters; missing drivers/devices never prevent other controls."""
import logging
import math
import threading
import time
from pathlib import Path

from visuals import screen

log = logging.getLogger(__name__)


def open_camera(cv2, source='auto'):
    if source == 'auto':
        # Stable USB names first; a webcam need not be /dev/video0.
        stable = sorted(Path('/dev/v4l/by-id').glob('*-video-index0'))
        devices = sorted(Path('/dev').glob('video[0-9]*'),
                         key=lambda p: int(p.name[5:]))
        usb = [p for p in devices
               if '/usb' in str((Path('/sys/class/video4linux') / p.name / 'device').resolve())]
        candidates = list(dict.fromkeys(str(p.resolve()) for p in stable + usb))
        if not candidates:
            raise RuntimeError('No USB webcam found. Check the USB connection and whether Linux detects the webcam.')
    else:
        candidates = [int(source) if str(source).isdigit() else str(source)]
    failures = []
    for device in candidates:
        cap = None
        try:
            log.info('Trying USB camera %s', device)
            cap = cv2.VideoCapture(device, cv2.CAP_V4L2)
            if not cap.isOpened():
                failures.append(f'{device}: cannot open (busy, permissions, or not a capture device)')
                continue
            # Keep the camera's negotiated format; do not demand an unsupported
            # 320x240 capture mode. Resize frames in software for hand tracking.
            for _ in range(10):
                ok, frame = cap.read()
                if ok and frame is not None and frame.size:
                    log.info('Using USB camera %s (%sx%s)', device, frame.shape[1], frame.shape[0])
                    selected, cap = cap, None
                    return selected
                time.sleep(.1)
            failures.append(f'{device}: opened but no frames after warm-up')
        except Exception as exc:
            failures.append(f'{device}: {exc}')
        finally:
            if cap is not None:
                cap.release()
    raise RuntimeError('No readable USB webcam. ' + '; '.join(failures)
                       + '. Close other camera apps; select a device with --camera-device /dev/videoN.')


def hand_signal(points):
    """Pinch distance relative to palm width, independent of camera distance."""
    def distance(a, b):
        return math.hypot(points[a].x - points[b].x, points[a].y - points[b].y)
    scale = distance(5, 17)
    if scale < 0.01:
        return None
    # Pinch is the thumb meeting any fingertip; open is every fingertip well away from it
    closest = min(distance(4, tip) for tip in (8, 12, 16, 20)) / scale
    return 'pinch' if closest < 0.45 else 'open' if closest > 0.9 else None


class Hardware:
    def __init__(self, camera=True, near=100, far=20, camera_device='auto'):
        self.readers = {}
        self.controls = {}
        self.errors = {}
        self.closers = []
        self.stop = threading.Event()
        self.camera_value = None
        self.camera_thread = None
        self.near, self.far = near, far
        try:
            import board
            self.i2c = board.I2C()
            deadline = time.monotonic() + 2
            while not self.i2c.try_lock():
                if time.monotonic() > deadline:
                    raise RuntimeError('I2C bus busy')
                time.sleep(.01)
            try:
                addresses = set(self.i2c.scan())
            finally:
                self.i2c.unlock()
            log.info('I2C addresses: %s', ', '.join(hex(a) for a in addresses))
            probes = [(0x36, self.encoder), (0x20, self.joystick),
                      (0x6F, self.button), (0x5A, self.touch),
                      (0x60, self.proximity), (0x29, self.tof), (0x39, self.color)]
            for address, probe in probes:
                if address in addresses:
                    try:
                        probe()
                    except Exception as exc:
                        log.warning('Cannot initialize %s at %s: %s', probe.__name__, hex(address), exc)
        except Exception as exc:
            log.warning('I2C discovery unavailable: %s', exc)
        if camera:
            try:
                self.camera(camera_device)
            except Exception as exc:
                log.warning('Camera unavailable: %s', exc)

    def add(self, name, kind, read):
        self.controls[name] = kind
        self.readers[name] = read
        log.info('Detected %s', name)

    def encoder(self):
        from adafruit_seesaw import seesaw, rotaryio
        device = seesaw.Seesaw(self.i2c, addr=0x36)
        if device.get_version() >> 16 != 4991:
            raise ValueError('Not the expected rotary encoder')
        encoder = rotaryio.IncrementalEncoder(device)
        origin = encoder.position
        # Clockwise counts up on this encoder
        self.add('encoder', 'encoder', lambda: encoder.position - origin)

    def joystick(self):
        import qwiic_joystick
        device = qwiic_joystick.QwiicJoystick()
        if not device.connected or device.begin() is False:
            raise ValueError('Joystick did not respond')
        # Readings span 0..1023 and rest near 512
        def read():
            x, y = device.horizontal - 512, 512 - device.vertical
            if max(abs(x), abs(y)) < 200:
                return 'neutral'
            return ('right' if x > 0 else 'left') if abs(x) > abs(y) else ('up' if y > 0 else 'down')
        self.add('joystick', 'joystick', read)

    def button(self):
        import qwiic_button
        device = qwiic_button.QwiicButton()
        if device.begin() is False:
            raise ValueError('Button did not respond')
        self.add('red_button', 'button', lambda: 'press' if device.is_button_pressed() else 'released')

    def touch(self):
        import adafruit_mpr121
        device = adafruit_mpr121.MPR121(self.i2c)
        def read():
            touched = [str(pad) for pad, value in enumerate(device.touched_pins) if value]
            return touched[0] if touched else 'released'
        self.add('touch', 'touch', read)

    def proximity(self):
        import qwiic_proximity
        device = qwiic_proximity.QwiicProximity()
        if not device.connected or device.begin() is False:
            raise ValueError('Proximity sensor did not respond')
        def read():
            value = device.get_proximity()
            return 'near' if value >= self.near else 'far' if value <= self.far else 'middle'
        self.add('proximity', 'distance', read)

    def tof(self):
        import qwiic_vl53l1x
        device = qwiic_vl53l1x.QwiicVL53L1X()
        status = device.sensor_init()
        if status not in (None, 0):
            raise ValueError(f'Distance sensor initialization failed: {status}')
        device.start_ranging()
        self.closers.append(device.stop_ranging)
        def read():
            value = device.get_distance()
            if value <= 0:
                return None
            return 'near' if value < 100 else 'far' if value > 250 else 'middle'
        self.add('distance', 'distance', read)

    def color(self):
        from adafruit_apds9960.apds9960 import APDS9960
        device = APDS9960(self.i2c)
        device.enable_color = True
        def read():
            if not device.color_data_ready:
                return None
            r, g, b, clear = device.color_data
            values = sorted(zip((r, g, b), ('red', 'green', 'blue')), reverse=True)
            if clear < 10 or values[0][0] < values[1][0] * 1.25:
                return None
            return values[0][1]
        self.add('color', 'color', read)

    def camera(self, source='auto'):
        import cv2
        import mediapipe as mp
        cap = open_camera(cv2, source)
        # Same pinned MediaPipe API as Lab 5; use keyword arguments to avoid
        # the positional-argument bug in the old HandTrackingModule wrapper.
        try:
            hands = mp.solutions.hands.Hands(max_num_hands=1,
                        min_detection_confidence=.5, min_tracking_confidence=.5)
        except Exception:
            cap.release()
            raise
        def worker():
            try:
                while not self.stop.is_set():
                    ok, frame = cap.read()
                    if not ok or frame is None or not frame.size:
                        self.camera_value = None
                        self.stop.wait(.1)
                        continue
                    frame = cv2.flip(frame, 1)  # Player's left is screen left.
                    height, width = frame.shape[:2]
                    frame = cv2.resize(frame, (320, max(1, round(height * 320 / width))))
                    result = hands.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    self.camera_value = (hand_signal(result.multi_hand_landmarks[0].landmark)
                                         if result.multi_hand_landmarks else None)
                    self.stop.wait(.04)
            except Exception:
                log.exception('Camera worker stopped')
            finally:
                self.camera_value = None
                cap.release()
                hands.close()
        self.camera_thread = threading.Thread(target=worker, daemon=True)
        self.camera_thread.start()
        self.add('camera', 'camera', lambda: self.camera_value)

    def poll(self):
        values = {}
        for name, read in self.readers.items():
            try:
                values[name] = read()
                self.errors.pop(name, None)
            except Exception as exc:
                if name not in self.errors:
                    log.warning('%s read failed: %s', name, exc)
                self.errors[name] = str(exc)
                values[name] = None
        return values

    def close(self):
        self.stop.set()
        if self.camera_thread:
            self.camera_thread.join(timeout=2)
        for close in self.closers:
            close()


class Display:
    def __init__(self, headless=False):
        self.buttons = []
        self.last_buttons = [False, False]
        self.last_edge = [0., 0.]
        self.display = None
        self.last_text = None
        if headless:
            return
        try:
            import board
            import digitalio
            from adafruit_rgb_display import st7789
            self.backlight = digitalio.DigitalInOut(board.D22)
            self.backlight.switch_to_output(value=True)
            self.cs = digitalio.DigitalInOut(board.D5)
            self.dc = digitalio.DigitalInOut(board.D25)
            # Portrait; use rotation=180 if the picture is upside down
            self.display = st7789.ST7789(board.SPI(), cs=self.cs, dc=self.dc,
                    width=135, height=240, x_offset=53, y_offset=40,
                    rotation=0, baudrate=64000000)
            for pin in (board.D23, board.D24):
                b = digitalio.DigitalInOut(pin)
                b.switch_to_input(pull=digitalio.Pull.UP)
                self.buttons.append(b)
        except Exception as exc:
            log.warning('PiTFT unavailable; using terminal: %s', exc)
            self.display = None

    def edges(self):
        edges = []
        now = time.monotonic()
        for i, button in enumerate(self.buttons):
            pressed = not button.value
            if pressed != self.last_buttons[i] and now - self.last_edge[i] >= .04:
                self.last_buttons[i] = pressed
                self.last_edge[i] = now
                if pressed:
                    edges.append('AB'[i])
        return edges

    def show(self, lines, cells, now, **style):
        if not self.display:
            text = (tuple(lines), tuple((kind, value) for kind, value, _ in cells), style.get('border'))
            if text != self.last_text:
                print(' | '.join(lines), ' '.join(f'{kind}={value}' for kind, value, _ in cells),
                      style.get('border') or '', flush=True)
                self.last_text = text
            return
        self.display.image(screen(lines, cells, now, **style))

    def close(self):
        for button in self.buttons:
            button.deinit()
        if self.display:
            self.backlight.value = False
            for pin in (self.backlight, self.cs, self.dc):
                pin.deinit()
