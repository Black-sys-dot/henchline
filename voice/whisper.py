"""Whisper, the chime, and the settings both push-to-talk and hands-free read
from ~/.config/ptt/config.json."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from voice.state import log

CONFIG = Path.home() / ".config/ptt/config.json"
AC_ONLINE = Path("/sys/class/power_supply/AC/online")
# Names Whisper otherwise hears as "cloud" and the like. Keep it short: a long
# list (and hyphenated words) made Whisper hyphenate and drop ordinary words.
DEFAULT_HOTWORDS = ["Claude", "Cursor", "Codex", "Kokoro"]


@dataclass
class Config:
    model: str = "small"
    device: str = "auto"  # auto: GPU on AC, CPU on battery; or cpu / cuda
    language: str = "en"  # "auto" lets Whisper detect it
    tone: bool = True  # tone marks for agent windows
    hotwords: list[str] = field(default_factory=lambda: list(DEFAULT_HOTWORDS))
    chime: bool = True
    chime_sound: str = "/usr/share/sounds/freedesktop/stereo/complete.oga"
    keys: list[str] = field(default_factory=lambda: ["F23"])  # the laptop Copilot key
    chords: list[list[str]] = field(default_factory=lambda: [["RIGHTCTRL", "RIGHTSHIFT"]])

    @classmethod
    def load(cls, log_to: Path) -> Config:
        """The file's settings over the defaults; unknown keys are ignored."""
        try:
            data = json.loads(CONFIG.read_text())
        except FileNotFoundError:
            data = {}
        except ValueError as exc:
            log(log_to, f"bad config: {exc}")
            data = {}
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def save(self) -> None:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(asdict(self), indent=2) + "\n")

    def want_cuda(self) -> bool:
        if self.device in ("cpu", "cuda"):
            return self.device == "cuda"
        try:
            return AC_ONLINE.read_text().strip() != "0"
        except OSError:
            return True


def load_model(cfg: Config, cuda: bool, log_to: Path):
    from faster_whisper import WhisperModel

    if cuda:
        try:
            model = WhisperModel(cfg.model, device="cuda", compute_type="float16")
            log(log_to, f"model {cfg.model} on cuda")
            return model
        except Exception as exc:
            log(log_to, f"cuda model failed ({exc}); using cpu")
    model = WhisperModel(cfg.model, device="cpu", compute_type="int8")
    log(log_to, f"model {cfg.model} on cpu")
    return model


def transcribe(model, path: Path, cfg: Config, log_to: Path) -> tuple[str, str]:
    """The plain transcript, and the one with tone marks for agent windows."""
    segments, _info = model.transcribe(
        str(path), language=None if cfg.language in ("", "auto") else cfg.language, vad_filter=True,
        beam_size=1, word_timestamps=cfg.tone, hotwords=", ".join(cfg.hotwords) or None,
    )
    segments = list(segments)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    rich = text
    if cfg.tone and text:
        try:
            from voice import tone

            words = [(w.start, w.end, w.word) for seg in segments for w in seg.words or []]
            rich = tone.annotate(path, words, text)
        except Exception as exc:  # tone is extra; the words must still get through
            log(log_to, f"tone failed: {exc}")
    return text, rich


def ding(cfg: Config) -> None:
    if cfg.chime and os.path.exists(cfg.chime_sound):
        subprocess.Popen(["pw-play", cfg.chime_sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


class Transcriber:
    """A loaded Whisper model that moves itself to the CPU when the GPU runs out of memory."""

    def __init__(self, cfg: Config, model, log_to: Path) -> None:
        self.cfg, self.model, self.log_to = cfg, model, log_to
        self.on_cpu = False

    def run(self, use):
        """use(model); if the GPU is full, move Whisper to the CPU and try again."""
        try:
            return use(self.model)
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower() or self.on_cpu:
                raise
            log(self.log_to, "GPU out of memory: Whisper moves to the CPU")
            self.model, self.on_cpu = load_model(self.cfg, False, self.log_to), True
            return use(self.model)

    def quick_text(self, audio) -> str:
        """A fast, plain transcript of a little audio (16 kHz floats): no tone, no voice detection."""
        return self.run(lambda model: " ".join(
            seg.text for seg in model.transcribe(audio, language="en", beam_size=1, vad_filter=False)[0]))

    def file(self, path: Path) -> tuple[str, str]:
        """(plain text, text with tone marks) of a recording."""
        return self.run(lambda model: transcribe(model, path, self.cfg, self.log_to))
