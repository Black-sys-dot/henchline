#!/usr/bin/env -S sh -c 'exec "$HOME/.local/share/copilot-ptt/venv/bin/python3" -E -s "$0" "$@"'
"""Score Whisper models on the user's own voice (asr_collect.py's test set).

    asr_bench.py [--strict] [--names] [MODEL ...]      (default: small medium distil-large-v3 large-v3-turbo)

Hints are the settings' hotwords (~/.config/ptt/config.json), as a turn
uses them; --names: the agent names only.

Each model runs on the GPU (float16) and on the CPU (int8, hands-free's
thread count), with the settings a hands-free turn uses (voice activity
filter, beam 1, the agent names as hints). Per model and device: word error
rate against the sentences the user was asked to say, the median and worst
seconds per take, CPU seconds per take, on the GPU the memory it holds and
the energy it drew above idle (nvidia-smi, sampled every 0.1 s), and the CPU
package's energy above idle when its counter (RAPL) is readable: it is
root-only, so `sudo chmod o+r` it for the run and back after. Battery
matters: the dGPU sleeps on battery (RTD3), and waking it per turn costs
more than the transcription.
"""

from __future__ import annotations

import json
import re
import resource
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # the repository, for voice/
from voice.state import HANDSFREE_DATA  # noqa: E402
from voice.whisper import CPU_THREADS, DEFAULT_HOTWORDS, Config  # noqa: E402

SET = HANDSFREE_DATA / "asr-set"
MODELS = ["small", "medium", "distil-large-v3", "large-v3-turbo"]
RAPL = Path("/sys/class/powercap/intel-rapl:0/energy_uj")  # the CPU package, in microjoules


def package_joules() -> float | None:
    try:
        return int(RAPL.read_text()) / 1e6
    except (OSError, ValueError):
        return None


def joules_between(start: float | None, end: float | None) -> float | None:
    """The counter wraps at max_energy_range_uj: a smaller end means it went round once."""
    if start is None or end is None:
        return None
    if end < start:
        try:
            end += int((RAPL.parent / "max_energy_range_uj").read_text()) / 1e6
        except (OSError, ValueError):
            return None
    return end - start


def package_idle_watts(seconds: float = 3.0) -> float | None:
    start = package_joules()
    time.sleep(seconds)
    joules = joules_between(start, package_joules())
    return None if joules is None else joules / seconds


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace("-", " "))


def wer(reference: str, heard: str) -> tuple[int, int]:
    """(word edits, reference words): substitutions, insertions and deletions."""
    ref, hyp = words(reference), words(heard)
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1], len(ref)


def gpu(query: str) -> float:
    out = subprocess.run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.split()
    return float(out[0]) if out else 0.0


class PowerMeter(threading.Thread):
    """GPU power, sampled every 0.1 s while it runs; joules above the idle draw it started with."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.idle, self.joules, self.stop = gpu("power.draw"), 0.0, threading.Event()

    def run(self) -> None:
        while not self.stop.wait(0.1):
            self.joules += max(0.0, gpu("power.draw") - self.idle) * 0.1


def bench(name: str, device: str, takes: dict[str, str], idle_w: float | None, hints: list[str]) -> dict:
    from faster_whisper import WhisperModel

    before = gpu("memory.used")
    if device == "cuda":
        model = WhisperModel(name, device="cuda", compute_type="float16")
    else:
        model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=CPU_THREADS)
    held = gpu("memory.used") - before if device == "cuda" else 0.0
    first = next(iter(takes))
    list(model.transcribe(str(SET / first), language="en")[0])  # warm up: the first run loads kernels
    edits = total = 0
    seconds, misheard = [], []
    meter = PowerMeter()
    meter.start()
    cpu0 = resource.getrusage(resource.RUSAGE_SELF)
    pkg0, wall0 = package_joules(), time.monotonic()
    for take, reference in takes.items():
        start = time.monotonic()
        segments, _ = model.transcribe(str(SET / take), language="en", vad_filter=True, beam_size=1,
                                       hotwords=", ".join(hints) or None)
        heard = " ".join(s.text for s in segments)
        seconds.append(time.monotonic() - start)
        e, n = wer(reference, heard)
        edits, total = edits + e, total + n
        if e:
            misheard.append((take, heard.strip()))
    cpu1 = resource.getrusage(resource.RUSAGE_SELF)
    pkg1, wall = package_joules(), time.monotonic() - wall0
    spent = joules_between(pkg0, pkg1)
    pkg_j = None if spent is None or idle_w is None else max(0.0, spent - idle_w * wall) / len(takes)
    meter.stop.set()
    meter.join()
    del model
    return {"model": name, "device": device, "wer": edits / max(total, 1),
            "median_s": statistics.median(seconds), "worst_s": max(seconds),
            "cpu_s_per_take": (cpu1.ru_utime + cpu1.ru_stime - cpu0.ru_utime - cpu0.ru_stime) / len(takes),
            "gpu_mb": held, "gpu_j_per_take": meter.joules / len(takes) if device == "cuda" else 0.0,
            "cpu_j_per_take": pkg_j,
            "misheard": misheard}


def main() -> int:
    global SET
    if "--strict" in sys.argv:  # the word-for-word round
        sys.argv.remove("--strict")
        SET = SET / "strict"
    if not (SET / "set.json").exists():
        print(f"no test set in {SET}: record one with asr_collect.py{' --strict' if SET.name == 'strict' else ''}",
              file=sys.stderr)
        return 1
    # The hints a turn uses: the settings' hotwords (the user's vocabulary), or --names for the agent names only
    names_only = "--names" in sys.argv
    if names_only:
        sys.argv.remove("--names")
    hints = list(DEFAULT_HOTWORDS) if names_only else Config.load(Path("/dev/null")).hotwords
    print(f"hints: {', '.join(hints)}")
    takes = json.loads((SET / "set.json").read_text())
    takes = {take: text for take, text in takes.items() if (SET / take).exists()}
    results = []
    idle_w = package_idle_watts()
    print(f"CPU package idle: {idle_w:.1f} W" if idle_w is not None else "CPU energy counter not readable: no CPU joules")
    for name in sys.argv[1:] or MODELS:
        for device in ("cuda", "cpu"):
            result = bench(name, device, takes, idle_w, hints)
            results.append(result)
            print(f"{name:16s} {device:4s}  WER {result['wer']:6.1%}  median {result['median_s']:.2f} s  "
                  f"worst {result['worst_s']:.2f} s  CPU {result['cpu_s_per_take']:.2f} s/take  "
                  f"GPU {result['gpu_mb']:.0f} MB, {result['gpu_j_per_take']:.1f} J/take"
                  + (f", CPU {result['cpu_j_per_take']:.1f} J/take" if result["cpu_j_per_take"] is not None else ""),
                  flush=True)
    (SET / "bench.json").write_text(json.dumps(results, indent=1))
    print(f"{len(takes)} takes; details (with what each model misheard) in {SET / 'bench.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
