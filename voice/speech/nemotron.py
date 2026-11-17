"""Nemotron streaming speech recognition: words heard while the user is still talking.

Whisper transcribes a turn once it has ended, so the wait after the last word is the whole transcription (about
1.6 s on the CPU, on battery). NVIDIA's Nemotron Speech Streaming is built for live captioning: fed 0.56 s at a
time as the turn is recorded, it has only the tail left when the user stops, about 0.15 s. Measured on the user's
own recordings (2026-11-16): word errors level with Whisper small, 13 of 20 of their names with the hint words at
strength 2.0 (Whisper 16). It runs on the CPU, so the GPU can stay asleep on battery.

The model (sherpa-onnx's int8 export) and a word-piece vocabulary for the hint words, built from NVIDIA's own
tokenizer, live in HANDSFREE_DATA/nemotron (`install.sh handsfree` fetches them). Without them, or if anything
fails, hands-free uses Whisper as before.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from pathlib import Path

import numpy as np

from voice.state import HANDSFREE_DATA, log

MODEL_DIR = HANDSFREE_DATA / "nemotron"
THREADS = 4  # more threads were slower on this laptop (11: 0.54 s per second of audio; 4: 0.24 s)
RATE = 16000
TAIL = 0.4  # seconds of silence after the last word, so its last frames are decoded
# Over 2.0 the hint words start appearing in silence ("Claude Claude Claude, can you…", strength 2.5)
HINT_SCORE = 2.0


def available() -> bool:
    return all((MODEL_DIR / name).exists() for name in
               ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt", "bpe.vocab"))


def build_vocab(tokenizer_json: Path, out: Path) -> None:
    """sherpa-onnx's word-piece vocabulary for the hint words, from NVIDIA's tokenizer: each piece with its
    merge rank as its score (earlier pieces are preferred, as the tokenizer itself prefers them)."""
    vocab = json.loads(tokenizer_json.read_text())["model"]["vocab"]
    out.write_text("".join(f"{piece}\t{-float(i)}\n" for piece, i in sorted(vocab.items(), key=lambda kv: kv[1])
                           if not piece.startswith("<")))


def words_of(tokens: list[str], times: list[float], ends: list[float]) -> list[tuple[float, float, str]]:
    """(start, end, word) from the model's word pieces (" Ber", "ze", "lius": a new word starts with a space, or
    "▁" in the tokenizer's own spelling), for the tone marks."""
    words: list[list] = []
    for piece, start, end in zip(tokens, times, ends):
        if piece.startswith(("▁", " ")) or not words:
            words.append([start, end, " " + piece.lstrip("▁ ")])  # spaced as Whisper's: tone joins them
        else:
            words[-1][1], words[-1][2] = end, words[-1][2] + piece
    return [(s, e, w) for s, e, w in words if w.strip()]


def tidy(text: str, hints: list[str]) -> str:
    """A hint word said once, not repeated: boosted, it can be written again in the silence around the words."""
    for word in hints:
        text = re.sub(rf"\b({re.escape(word)})(?:[\s,.]+\1\b)+", r"\1", text, flags=re.IGNORECASE)
    return text.strip()


class Live:
    """One turn, decoded on its own thread as frames arrive, so recording never waits for it."""

    def __init__(self, recognizer, log_to: Path) -> None:
        self.recognizer, self.log_to = recognizer, log_to
        self.stream = recognizer.create_stream()
        self.frames: queue.Queue = queue.Queue()
        self.failed = False
        self.lock = threading.Lock()  # decoding and a look at the words so far take turns
        self.thread = threading.Thread(target=self._decode, name="nemotron", daemon=True)
        self.thread.start()

    def feed(self, frame: np.ndarray) -> None:
        """16 kHz floats, as recorded."""
        self.frames.put(frame)

    def _decode(self) -> None:
        try:
            while (frame := self.frames.get()) is not None:
                with self.lock:
                    self.stream.accept_waveform(RATE, frame)
                    while self.recognizer.is_ready(self.stream):
                        self.recognizer.decode_stream(self.stream)
        except Exception as exc:  # Whisper takes the turn instead
            self.failed = True
            log(self.log_to, f"nemotron failed: {exc}")

    def finish(self, timeout: float = 10.0) -> tuple[str, list[tuple[float, float, str]]] | None:
        """The words, and their times; None if it failed (Whisper then transcribes the turn)."""
        self.frames.put(np.zeros(int(TAIL * RATE), dtype=np.float32))
        self.frames.put(None)
        self.thread.join(timeout)
        if self.failed or self.thread.is_alive():
            return None
        try:
            self.stream.input_finished()
            while self.recognizer.is_ready(self.stream):
                self.recognizer.decode_stream(self.stream)
            result = self.recognizer.get_result_all(self.stream)
            times = list(result.timestamps)
            ends = times[1:] + [times[-1] + 0.08] if times else []
            return result.text.strip(), words_of(list(result.tokens), times, ends)
        except Exception as exc:
            log(self.log_to, f"nemotron failed at the end: {exc}")
            return None

    def so_far(self) -> str:
        """The words heard until now (the last half second may still be coming): no second transcription needed."""
        with self.lock:
            return str(self.recognizer.get_result(self.stream))

    def cancel(self) -> None:
        self.frames.put(None)


class Streamer:
    """The model, loaded once; a Live per turn."""

    def __init__(self, hints: list[str], log_to: Path) -> None:
        import sherpa_onnx

        self.log_to = log_to
        words = MODEL_DIR / "hotwords.txt"
        words.write_text("".join(f"{h}\n" for h in hints if h.strip()))
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(MODEL_DIR / "tokens.txt"), encoder=str(MODEL_DIR / "encoder.int8.onnx"),
            decoder=str(MODEL_DIR / "decoder.int8.onnx"), joiner=str(MODEL_DIR / "joiner.int8.onnx"),
            num_threads=THREADS, decoding_method="modified_beam_search", max_active_paths=4,
            hotwords_file=str(words), hotwords_score=HINT_SCORE, modeling_unit="bpe",
            bpe_vocab=str(MODEL_DIR / "bpe.vocab"))
        log(log_to, f"nemotron streaming on the cpu ({THREADS} threads, {len(hints)} hint words)")

    def begin(self) -> Live:
        return Live(self.recognizer, self.log_to)
