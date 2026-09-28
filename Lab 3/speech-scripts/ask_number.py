#!/usr/bin/env python3
"""Ask a question out loud, wait for a spoken number, print what you said."""

import argparse
import re
import sys
import time
from pathlib import Path

import numpy as np
import sherpa_onnx
import sounddevice as sd
from faster_whisper import WhisperModel
from piper import PiperVoice
from word2number import w2n

SAMPLE_RATE = 16000
LAB_DIR = Path(__file__).resolve().parent.parent
DEFAULT_VAD = LAB_DIR / "models" / "silero_vad.onnx"
DEFAULT_VOICE = LAB_DIR / "voices" / "en_US-lessac-medium.onnx"


def parse_number(text: str):
    """Pulls a number out of a transcript, as digits or as words.

    Returns a float, or None if there is nothing number-shaped in there.
    Whisper writes large numbers as digits and small ones as words, so both
    paths get used in practice.
    """
    digits = re.search(r"-?\d[\d,]*(?:\.\d+)?", text)
    if digits:
        return float(digits.group().replace(",", ""))

    words = text.lower()
    # "a hundred and five" confuses word2number into 500 — it wants the
    # multiplier spelled out, the way "one hundred and five" is.
    words = re.sub(r"\b(?:a|an)\s+(hundred|thousand|million)\b", r"one \1", words)

    try:
        # word2number drops the words it doesn't recognize, so a whole
        # sentence ("I slept about seven hours") can go straight in.
        value = float(w2n.word_to_num(words))
    except (ValueError, IndexError, AttributeError):
        return None  # nothing in there it could read as a number

    if re.search(r"\bhalf\b", text.lower()):
        value += 0.5  # "seven and a half": word2number stops at the seven
    return value


class Speaker:
    """Synthesizes with Piper and plays through the default output device."""

    def __init__(self, voice_path: Path) -> None:
        self.voice = PiperVoice.load(str(voice_path))

    def say(self, text: str) -> None:
        print(f"Says:  {text}")
        for chunk in self.voice.synthesize(text):
            audio = np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16)
            sd.play(audio, samplerate=chunk.sample_rate)
            sd.wait()


class Listener:
    """One VAD-segmented utterance at a time, transcribed."""

    def __init__(self, vad_model: Path, whisper_model: str, min_silence: float) -> None:
        self.recognizer = WhisperModel(whisper_model, device="cpu", compute_type="int8")
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(vad_model)
        config.silero_vad.min_silence_duration = min_silence
        config.sample_rate = SAMPLE_RATE
        self.vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        self.window = config.silero_vad.window_size

    def next_utterance(self, timeout: float) -> str:
        """Blocks until someone finishes speaking. Returns "" if nobody does."""
        # Anything the VAD buffered while we were talking is our own voice
        # coming back through the microphone. Throw it away before listening.
        while not self.vad.empty():
            self.vad.pop()

        buffer = np.empty(0, dtype=np.float32)
        samples_per_read = int(0.1 * SAMPLE_RATE)
        deadline = time.perf_counter() + timeout

        with sd.InputStream(channels=1, dtype="float32", samplerate=SAMPLE_RATE) as stream:
            while time.perf_counter() < deadline:
                chunk, _ = stream.read(samples_per_read)
                buffer = np.concatenate([buffer, chunk.reshape(-1)])

                while len(buffer) > self.window:
                    self.vad.accept_waveform(buffer[:self.window])
                    buffer = buffer[self.window:]

                while not self.vad.empty():
                    utterance = np.array(self.vad.front.samples, dtype=np.float32)
                    self.vad.pop()
                    segments, _ = self.recognizer.transcribe(utterance, beam_size=1)
                    text = " ".join(s.text.strip() for s in segments)
                    if text:
                        return text
                    # Noise, not words. Keep waiting, but don't reset the clock.
        return ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--question", default="What is your number?",
                        help="what to ask out loud")
    parser.add_argument("--timeout", type=float, default=12.0,
                        help="seconds to wait for an answer each try (default: 12)")
    parser.add_argument("--model", default="tiny.en",
                        help="whisper model size (default: tiny.en)")
    parser.add_argument("--vad-model", type=Path, default=DEFAULT_VAD)
    parser.add_argument("--voice", type=Path, default=DEFAULT_VOICE)
    parser.add_argument("--min-silence", type=float, default=0.6,
                        help="seconds of silence that end the answer (default: 0.6)")
    args = parser.parse_args()

    for path, what in [(args.vad_model, "VAD model"), (args.voice, "Piper voice")]:
        if not path.is_file():
            sys.exit(f"{what} not found at {path}. Run ./setup.sh first.")

    print("Loading models...", flush=True)
    speaker = Speaker(args.voice)
    listener = Listener(args.vad_model, args.model, args.min_silence)
    print("Ready.\n")

    prompt = args.question
    while True:
        speaker.say(prompt)
        transcript = listener.next_utterance(args.timeout)
        print(f" Heard: {transcript or '(nothing)'}")

        value = parse_number(transcript) if transcript else None
        if value is not None:
            speaker.say(f"Got {int(value)}.")
            print(f"Number: {int(value)}")
            return
        else:
            speaker.say("No number heard.")
            print(f"No number heard.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
