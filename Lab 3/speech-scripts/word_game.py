#!/usr/bin/env python3
"""Word Game Toy: build a sentence together, one word at a time.

Press button A to start. You and the toy take turns saying one word; say
"period" to end the sentence. Button B puts the toy to sleep.
The screen shows whichever word was just said: orange for the toy, white for you.

    python word_game.py
"""

import random
import re
import sys
import time
from pathlib import Path

import adafruit_rgb_display.st7789 as st7789
import board
import digitalio
import numpy as np
import sherpa_onnx
import sounddevice as sd
import soundfile as sf
from faster_whisper import WhisperModel
from piper import PiperVoice
from PIL import Image, ImageDraw, ImageFont

from claude_llm import ask

SAMPLE_RATE = 16000
LAB_DIR = Path(__file__).resolve().parent.parent
VAD_MODEL = LAB_DIR / "models" / "silero_vad.onnx"
VOICE = LAB_DIR / "voices" / "en_US-lessac-medium.onnx"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

MODEL = "claude-opus-5"
EFFORT = "low"              # thinks just enough to keep the sentence grammatical
WHISPER_MODEL = "tiny.en"
TOY_COLOR = "#E8742A"
USER_COLOR = "#FFFFFF"
CLIP_DIR = Path("/tmp/claude-1000/-home-pi-Interactive-Lab-Hub/ddede258-59a6-4b16-b726-0fc0964a8843/scratchpad/clips")   # TEMP: debugging recognition
UNCLEAR = "(unclear)"       # what the listener returns when Whisper can't make out the speech

STARTERS = ["Yesterday", "Somewhere", "Suddenly", "Once", "Tomorrow", "Tonight",
            "Every", "My", "The", "Grandma", "Nobody", "Last", "Under", "Behind",
            "Inside", "Secretly", "Sometimes", "Before", "After", "Everybody",
            "Deep", "Far", "Long", "Today", "Three", "A", "Our", "Seven",
            "Unfortunately", "Luckily"]

RULES = ("Let's play the word game! We take turns saying one word to build a "
         "sentence. Say period to end it. Let's start, the first word is...")

TOY_PROMPT = """You are a friendly toy playing a word game. You and a person take turns adding one word to build a sentence together.
You are given the sentence so far and what a speech recognizer heard the person say next.
The recognizer is unreliable on single words and often writes a sound-alike: "Bye" for "I", "2" for "to", "So" or "There" for "the", "Both" for "of", "Hey" for "a".
First, decide which ONE word the person said. Keep what the recognizer heard unless it clearly does not fit; then swap it only for a word that sounds very similar.
Then add your own ONE word. Be a good teammate: figure out where the person seems to be taking the sentence and pick a natural word that helps it get there and makes sense. The sentence must stay grammatical and sensible; only add a small playful touch now and then when it still makes sense.
Reply with exactly two words separated by a space, the person's word then yours, with no punctuation.
Even when unsure, guess. Never explain or comment.
Never end the sentence yourself; only the person ends it, by saying "period"."""

REACT_PROMPT = """You are a friendly toy. You just finished building this sentence with a person.
React to it warmly in at most six words, like "Ha! What a busy president!". Plain text only."""


class Screen:
    """The MiniPiTFT, showing one word at a time."""

    def __init__(self) -> None:
        self.disp = st7789.ST7789(
            board.SPI(),
            cs=digitalio.DigitalInOut(board.D5),
            dc=digitalio.DigitalInOut(board.D25),
            rst=None,
            baudrate=64000000,
            width=135,
            height=240,
            x_offset=53,
            y_offset=40,
        )
        self.backlight = digitalio.DigitalInOut(board.D22)
        self.backlight.switch_to_output()
        self.width, self.height = 240, 135      # landscape

    def show(self, word: str, color: str) -> None:
        self.image = Image.new("RGB", (self.width, self.height))
        draw = ImageDraw.Draw(self.image)

        # Shrinks the font until the word fits, leaving room for the thinking dots
        size = 100
        while True:
            font = ImageFont.truetype(FONT, size)
            left, top, right, bottom = draw.textbbox((0, 0), word, font=font, anchor="mm")
            if (right - left <= self.width - 16 and bottom - top <= self.height - 44) or size <= 12:
                break
            size -= 4

        draw.text((self.width / 2, self.height / 2), word, font=font, fill=color, anchor="mm")
        self.disp.image(self.image, 90)
        self.backlight.value = True

    def thinking(self) -> None:
        """Adds three orange dots under the current word."""
        image = self.image.copy()
        draw = ImageDraw.Draw(image)
        for x in (self.width / 2 - 18, self.width / 2, self.width / 2 + 18):
            draw.ellipse((x - 5, self.height - 17, x + 5, self.height - 7), fill=TOY_COLOR)
        self.disp.image(image, 90)

    def off(self) -> None:
        self.disp.image(Image.new("RGB", (self.width, self.height)), 90)
        self.backlight.value = False


class Speaker:
    """Speaks with Piper and flashes each word on the screen as it is said."""

    def __init__(self, voice_path: Path, screen: Screen) -> None:
        self.voice = PiperVoice.load(str(voice_path))
        self.screen = screen

    def say(self, text: str) -> None:
        print(f"  toy: {text}")
        words = text.split()

        # Piper returns one chunk of audio per sentence
        for chunk in self.voice.synthesize(text):
            audio = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
            sd.play(audio, samplerate=chunk.sample_rate)

            # Guesses when each word is said from its share of the sentence's phonemes
            phoneme_words = "".join(chunk.phonemes).split()
            seconds = len(audio) / chunk.sample_rate
            total = sum(len(p) for p in phoneme_words)
            start = time.perf_counter()
            elapsed = 0.0
            for p in phoneme_words:
                if words:
                    self.screen.show(words.pop(0).strip(".,!?:;"), TOY_COLOR)
                elapsed += seconds * len(p) / total
                time.sleep(max(0.0, start + elapsed - time.perf_counter()))
            sd.wait()

        # Lets the last of the audio leave the speaker, so the mic doesn't hear the toy
        time.sleep(0.4)


class Listener:
    """One VAD-segmented utterance at a time, transcribed."""

    def __init__(self, vad_model: Path, whisper_model: str, min_silence: float,
                 screen: Screen) -> None:
        self.screen = screen
        self.recognizer = WhisperModel(whisper_model, device="cpu", compute_type="int8")
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(vad_model)
        config.silero_vad.min_silence_duration = min_silence
        config.silero_vad.min_speech_duration = 0.1      # catches short words like "I"
        config.sample_rate = SAMPLE_RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        self.window = config.silero_vad.window_size
        self.clip_count = 0

    def next_utterance(self, timeout: float, hint: str, stop_button) -> str | None:
        """Returns what was said, UNCLEAR if Whisper heard speech but no words,
        "" if nobody spoke, or None if stop_button was pressed.

        hint is the sentence so far. Whisper uses it as context, which helps it
        hear a single word correctly.
        """
        # Drops anything buffered while the toy was talking
        while not self.vad.empty():
            self.vad.pop()

        buffer = np.empty(0, dtype=np.float32)
        samples_per_read = int(0.1 * SAMPLE_RATE)
        deadline = time.perf_counter() + timeout

        with sd.InputStream(channels=1, dtype="float32", samplerate=SAMPLE_RATE) as stream:
            while time.perf_counter() < deadline:
                if not stop_button.value:
                    return None

                chunk, _ = stream.read(samples_per_read)
                buffer = np.concatenate([buffer, chunk.reshape(-1)])

                while len(buffer) > self.window:
                    self.vad.accept_waveform(buffer[:self.window])
                    buffer = buffer[self.window:]

                if not self.vad.empty():
                    utterance = np.array(self.vad.front.samples, dtype=np.float32)
                    self.vad.pop()
                    self.screen.thinking()

                    # Pads the clip with silence; Whisper often returns nothing when a word fills the whole clip
                    silence = np.zeros(SAMPLE_RATE // 2, dtype=np.float32)
                    segments, _ = self.recognizer.transcribe(
                        np.concatenate([silence, utterance, silence]), beam_size=1, initial_prompt=hint)
                    text = " ".join(s.text.strip() for s in segments)
                    # TEMP: saves the clip and logs its length, loudness and transcript
                    self.clip_count += 1
                    sf.write(CLIP_DIR / f"{self.clip_count:03d}.wav", utterance, SAMPLE_RATE)
                    print(f"  (clip {self.clip_count:03d}: {len(utterance) / SAMPLE_RATE:.2f}s, "
                          f"peak {np.abs(utterance).max():.2f}, hint {hint!r}, whisper {text!r})")
                    return text if re.search(r"[A-Za-z0-9]", text) else UNCLEAR
        return ""


def play_game(screen: Screen, speaker: Speaker, listener: Listener, stop_button) -> None:
    """Plays rounds until the user presses B, says goodbye, or stays quiet twice."""
    print("Playing.")
    first = random.choice(STARTERS)
    speaker.say(RULES)

    while True:
        words = [first]
        speaker.say(first + "!")
        silences = 0

        while True:
            # User's turn
            heard = listener.next_utterance(15, " ".join(words), stop_button)
            if heard is None:
                print("  (button B pressed)")
                return
            if heard == UNCLEAR:
                print("  (heard speech, but no words)")
                speaker.say("Say that again?")
                continue
            if heard == "":
                silences += 1
                print("  (nothing heard)")
                if silences == 2:
                    return
                speaker.say("Your turn!")
                continue
            silences = 0

            heard_words = re.findall(r"[a-z0-9']+", heard.lower())
            if "goodbye" in heard_words or "good bye" in heard.lower():
                speaker.say("Goodbye!")
                return
            if heard_words[0] == "period":
                print(f"  you: period    (heard: {heard})")
                screen.show("Period!", USER_COLOR)
                break

            # Asks Claude for the user's word, fixing mishearings, and the toy's next word
            screen.thinking()
            start = time.perf_counter()
            content = f"Sentence so far: {' '.join(words)}\nRecognizer heard: {heard}"
            reply = ask(TOY_PROMPT, [{"role": "user", "content": content}],
                        model=MODEL, max_tokens=2000, effort=EFFORT)
            found = re.findall(r"[A-Za-z']+", reply)
            found = ["I" if w.lower() == "i" else w.lower() for w in found]
            user_word = found[0] if found else heard_words[0]
            toy_word = found[1] if len(found) > 1 else ""
            print(f"  you: {user_word}    (heard: {heard}, toy thought for {time.perf_counter() - start:.2f}s)")

            words.append(user_word)
            screen.show(user_word, USER_COLOR)
            time.sleep(0.6)

            # Toy's turn; only the user can end the sentence
            if toy_word and toy_word != "period":
                words.append(toy_word)
                speaker.say(toy_word)

        # Reads the sentence back and reacts to it
        sentence = " ".join(words) + "."
        speaker.say(sentence)
        screen.thinking()
        reaction = ask(REACT_PROMPT, [{"role": "user", "content": sentence}],
                       model=MODEL, max_tokens=2000, effort=EFFORT)
        speaker.say(reaction)
        speaker.say("Again? Next first word:")
        first = random.choice(STARTERS)


def main() -> None:
    # Prints Whisper's symbols like "…" even when the terminal locale is not UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    print("Loading models...", flush=True)
    screen = Screen()
    speaker = Speaker(VOICE, screen)
    listener = Listener(VAD_MODEL, WHISPER_MODEL, min_silence=0.6, screen=screen)

    button_a = digitalio.DigitalInOut(board.D23)
    button_b = digitalio.DigitalInOut(board.D24)
    button_a.switch_to_input(pull=digitalio.Pull.UP)
    button_b.switch_to_input(pull=digitalio.Pull.UP)

    try:
        while True:
            screen.off()
            print("Press button A to play.")
            while button_a.value:
                time.sleep(0.02)
            play_game(screen, speaker, listener, button_b)
    finally:
        screen.off()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
