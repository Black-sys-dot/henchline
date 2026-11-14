#!/home/shekharu/.local/share/talkback/venv/bin/python3
"""Speak text with the local Kokoro voice on the default output.

    say.py [--wait] [--voice V] [TEXT ...]   text from the arguments, or stdin
    say.py --release [TEXT]       play this session's held clips (TEXT first, if given)
    say.py --drop                 throw away this session's held and queued clips
    say.py --stop                 cut off everything and empty the queue

Each call drops its text in a spool and returns at once. One detached worker
at a time (whoever holds the lock) speaks the spool oldest first, each clip
after the previous one has finished, sentence by sentence, and exits when
nothing is left. Every agent session has its own voice and its own clips; a
different speaker introduces itself first.

While push-to-talk records (its phase file), the worker goes quiet at once.
When the words went into an agent window, the clips queued so far are held
until their session releases or drops them, or for HOLD seconds, after
which they are dropped: never speak before the agent has heard the user.
Words that went anywhere else let them play on, from the start of the
sentence that was cut.

The voice is Kokoro on the CPU with a small thread cap, so the dGPU can
sleep. Tags such as [laugh] are dropped rather than read aloud.
"""

from __future__ import annotations

import fcntl
import json
import os
import queue
import re
import resource
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path.home() / ".local/share/talkback"
STATE = Path.home() / ".local/state/ptt"
SPOOL = STATE / "talkback.queue"
HOLDS = STATE / "talkback.holds"
LOCK = STATE / "talkback.lock"
PIDFILE = STATE / "talkback.pid"
PLAYING = STATE / "talkback.playing"
LAST = STATE / "talkback.last"
LOG = STATE / "talkback.log"
TAGS = re.compile(r"\[(?:laugh|chuckle|sigh|gasp|groan|cough|sniff|shush|clear throat|whispering)\]\s*")
PHASE = STATE / "phase"  # written by push-to-talk
TARGET = STATE / "target"  # written by push-to-talk
THREADS = int(os.environ.get("TALKBACK_THREADS", "4"))
RATE = 24000
PAUSE = 0.25  # seconds between sentences
PIECE = 0.1  # seconds of audio written between checks for a hold
HOLD = 90  # seconds a held clip waits for its session before it is dropped
VOICES = {"claude": "af_heart", "cursor": "af_bella"}
NAMES = {"claude": "Claude", "cursor": "Cursor"}
SYSTEM = {"handsfree"}
PLAYER = [
    "pacat", "--playback", "--raw", "--format=float32le", f"--rate={RATE}",
    "--channels=1", "--latency-msec=100", "--client-name=talkback",
    "--stream-name=talkback",
]


def log(line: str) -> None:
    with open(LOG, "a") as out:
        out.write(f"{time.strftime('%F %T')} {line}\n")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def write_json(path: Path, data: dict) -> None:
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def identity(args: list[str]) -> tuple[str, str]:
    """Which agent and session is calling: flags first, then the environment."""
    agent = session = ""
    for flag, value in zip(args, args[1:]):
        if flag == "--agent":
            agent = value
        elif flag == "--session":
            session = value
    if not agent:
        if os.environ.get("CLAUDECODE"):
            agent = "claude"
        elif "cursor" in os.environ.get("AI_AGENT", "").lower() or any(
            key.startswith("CURSOR_") for key in os.environ
        ):
            agent = "cursor"
        else:
            agent = "agent"
    if not session:
        session = (
            os.environ.get("CLAUDE_CODE_SESSION_ID")
            or os.environ.get("CURSOR_TRACE_ID")
            or os.getcwd()
        )
    key = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{agent}-{session}")[-120:]
    return agent, key


def recording() -> bool:
    state = read_json(PHASE)
    busy = state.get("phase") in ("recording", "transcribing")
    return busy and time.time() - state.get("at", 0) < 300  # ignore a stale file


# --- spool ------------------------------------------------------------------


def konsole_target() -> list[str]:
    """The Konsole tab this agent is really shown in, for hands-free replies.

    KONSOLE_DBUS_SESSION is only trusted when that tab's foreground process is
    one of our ancestors and no background Claude daemon (bg-pty-host) sits in
    between: a session moved into the daemon inherits the env of whichever tab
    started the daemon (on 2026-11-14 a different project's tab, which then got
    the user's hands-free replies). Then the tab is looked up by its
    "<workspace> : claude" title instead, and left empty when not unique.
    """
    service = os.environ.get("KONSOLE_DBUS_SERVICE", "")
    session = os.environ.get("KONSOLE_DBUS_SESSION", "")
    if not service:
        return ["", ""]

    def qdbus(*args: str) -> str:
        return subprocess.run(["qdbus", service, *args], capture_output=True, text=True).stdout.strip()

    ancestors, daemon, pid = [], False, os.getpid()  # nearest first
    while pid > 1:
        ancestors.append(pid)
        try:
            daemon |= b"bg-pty-host" in Path(f"/proc/{pid}/cmdline").read_bytes()
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    fg = qdbus(session, "foregroundProcessId") if session else ""
    if not daemon and fg.isdigit() and int(fg) in ancestors:
        return [service, session]
    # The tab is titled after the folder Claude started in, but the shell may
    # have cd'ed deeper since (pc_diagnostics/journal), so try every parent
    titles = {s: qdbus(s, "title", "1") for s in qdbus().split() if s.startswith("/Sessions/")}
    # Start from the agent's own folder (its process cwd: the shell running
    # say.py may be anywhere, e.g. ~/copilot-ptt), then the shell's folder
    # (the nearest one: further up may be the agent whose tab started a daemon)
    starts = []
    for pid in ancestors:
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")[0].decode()
        except OSError:
            continue
        if re.search(r"(^|/)claude(/|$)|cursor-agent|/agent$", command):
            starts.append(Path(os.readlink(f"/proc/{pid}/cwd")))
            break
    starts.append(Path.cwd())
    for folder in [f for start in starts for f in (start, *start.parents)]:
        tabs = [s for s, title in titles.items() if title == f"{folder.name} : claude"]
        if len(tabs) == 1:
            return [service, tabs[0]]
        if tabs:
            break  # several Claude tabs in that folder: do not guess
    return ["", ""]


def enqueue(agent: str, key: str, text: str, voice: str, at: int | None = None) -> Path:
    """Spool the text; `at` (ns) places it in the queue, default now."""
    item = SPOOL / f"{at or time.time_ns()}-{os.getpid()}.json"
    cwd = Path.cwd()
    write_json(item, {
        "agent": agent,
        "session": key,
        "voice": voice,
        "where": "home" if cwd == Path.home() else cwd.name,
        # Where hands-free mode types the user's reply to this agent
        "konsole": konsole_target(),
        "sentences": [s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()],
        "queued": time.time(),
    })
    return item


def clips() -> list[Path]:
    return sorted(SPOOL.glob("*.json"))


def held(key: str) -> bool:
    return (HOLDS / key).exists()


def hold(sessions: set[str]) -> None:
    for key in sessions:
        write_json(HOLDS / key, {"until": time.time() + HOLD})
    if sessions:
        log(f"held {', '.join(sorted(sessions))} for {HOLD}s")


def drop(key: str) -> None:
    for item in clips():
        if read_json(item).get("session") == key:
            item.unlink(missing_ok=True)
    (HOLDS / key).unlink(missing_ok=True)


def expire() -> None:
    for item in HOLDS.glob("[!.]*"):
        if read_json(item).get("until", 0) < time.time():
            drop(item.name)
            log(f"dropped {item.name}, nobody decided in time")


# --- worker -----------------------------------------------------------------


def lock() -> int | None:
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def spawn_worker() -> None:
    subprocess.Popen(
        [__file__, "--worker"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=open(LOG, "a"),
        start_new_session=True,
    )


class Speaker:
    def __init__(self) -> None:
        self._kokoro = None
        self.skip = False
        signal.signal(signal.SIGUSR1, self.on_skip)

    def on_skip(self, *_) -> None:
        self.skip = True

    @property
    def kokoro(self):
        if self._kokoro is None:
            import onnxruntime as rt
            from kokoro_onnx import Kokoro

            opts = rt.SessionOptions()
            opts.intra_op_num_threads = THREADS
            opts.inter_op_num_threads = 1
            session = rt.InferenceSession(
                str(ROOT / "kokoro-v1.0.onnx"), opts, providers=["CPUExecutionProvider"]
            )
            self._kokoro = Kokoro.from_session(session, str(ROOT / "voices-v1.0.bin"))
        return self._kokoro

    def create(self, text: str, voice: str):
        plain = TAGS.sub("", text).strip()  # Kokoro would read [laugh] aloud
        if not plain:
            return None
        return self.kokoro.create(plain, voice=voice, lang="en-us")[0]

    def play(self, item: Path) -> None:
        """Speak one clip; put back what is left if a hold cuts it."""
        import numpy as np

        clip = read_json(item)
        if not clip:
            item.unlink(missing_ok=True)
            return
        write_json(PLAYING, {"clip": item.name, "session": clip["session"]})
        item.unlink(missing_ok=True)
        sentences = clip["sentences"]
        # System notices (hands-free) neither introduce themselves nor become
        # the agent a hands-free reply goes to
        if clip["agent"] not in SYSTEM:
            if read_json(LAST).get("session") != clip["session"]:
                name = NAMES.get(clip["agent"], clip["agent"].capitalize())
                sentences = [f"{name} here, from {clip['where']}."] + sentences
            write_json(LAST, {"session": clip["session"], "agent": clip["agent"],
                              "konsole": clip.get("konsole", ["", ""])})
        voice = clip.get("voice") or VOICES.get(clip["agent"], "af_heart")
        ready: queue.Queue = queue.Queue(maxsize=2)
        done = threading.Event()

        def make() -> None:
            try:
                for sentence in sentences:
                    samples = self.create(sentence, voice)
                    if samples is None:  # a bare tag on Kokoro; keep the count right
                        samples = np.zeros(0, dtype="<f4")
                    while not done.is_set():
                        try:
                            ready.put(samples, timeout=0.2)
                            break
                        except queue.Full:
                            continue
            finally:
                ready.put(None)

        threading.Thread(target=make, daemon=True).start()
        player = subprocess.Popen(PLAYER, stdin=subprocess.PIPE)
        piece = int(RATE * PIECE)
        spoken, cut = 0, False
        self.skip = False
        try:
            while (samples := ready.get()) is not None:
                if spoken == 0:
                    log(f"{clip['agent']} first_audio={time.time() - clip['queued']:.2f}s")
                else:
                    samples = np.concatenate([np.zeros(int(RATE * PAUSE), "<f4"), samples])
                data = samples.astype("<f4")
                for start in range(0, len(data), piece):
                    if recording() or self.skip:
                        cut = True
                        break
                    player.stdin.write(data[start:start + piece].tobytes())
                    player.stdin.flush()
                if cut:
                    break
                spoken += 1
        finally:
            done.set()
            if cut:
                player.terminate()
            else:
                player.stdin.close()
            player.wait()
            PLAYING.unlink(missing_ok=True)
        if cut and not self.skip:
            # The intro, if any, was spoken; resume from the cut sentence
            rest = sentences[spoken:]
            if len(sentences) > len(clip["sentences"]):
                rest = sentences[max(spoken, 1):]
            write_json(item, {**clip, "sentences": rest})
            log(f"{clip['agent']} cut by push-to-talk, {len(rest)} sentences kept")


def after_input(since: float) -> None:
    """Push-to-talk just finished: hold clips if the words went to an agent."""
    waiting = {read_json(item).get("session") for item in clips()} - {None}
    if not waiting:
        return
    deadline = time.time() + 2
    while time.time() < deadline:
        target = read_json(TARGET)
        if target.get("at", 0) >= since:
            if target.get("agent"):
                hold(waiting)
            return
        time.sleep(0.1)
    # Nothing was pasted (too short, empty): nobody got new input, play on


def work() -> int:
    fd = lock()
    if fd is None:
        return 0  # another worker holds the lock and will see our text
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    PIDFILE.write_text(str(os.getpid()))
    t0, speaker, since = time.monotonic(), None, None
    while fd is not None:
        if recording():
            since = since or time.time()
            time.sleep(0.1)
            continue
        if since is not None:
            after_input(since - 1)
            since = None
        expire()
        ready = [item for item in clips() if not held(read_json(item).get("session", ""))]
        if ready:
            speaker = speaker or Speaker()
            speaker.play(ready[0])
            continue
        if clips() or any(HOLDS.glob("[!.]*")):
            time.sleep(0.2)
            continue
        # Let go, then look once more: text queued while we were letting go
        # either sees the lock free and starts its own worker, or is seen here
        PIDFILE.unlink(missing_ok=True)
        os.close(fd)
        fd = lock() if clips() else None
        if fd is not None:
            PIDFILE.write_text(str(os.getpid()))
    if speaker is not None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        log(
            f"worker done wall={time.monotonic() - t0:.1f}s "
            f"cpu={usage.ru_utime + usage.ru_stime:.1f}s threads={THREADS}"
        )
    return 0


# --- commands ---------------------------------------------------------------


def stop() -> None:
    for item in [*clips(), *HOLDS.iterdir()]:
        item.unlink(missing_ok=True)
    try:
        os.killpg(int(PIDFILE.read_text().strip()), signal.SIGTERM)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass
    PIDFILE.unlink(missing_ok=True)
    PLAYING.unlink(missing_ok=True)


def main() -> int:
    args = sys.argv[1:]
    SPOOL.mkdir(parents=True, exist_ok=True)
    HOLDS.mkdir(parents=True, exist_ok=True)
    commands = {"--stop", "--worker", "--release", "--drop"}
    command = next((arg for arg in args if arg in commands), "")
    if command == "--stop":
        stop()
        return 0
    if command == "--worker":
        return work()
    agent, key = identity(args)
    if command == "--release":
        # With text: say it first ("no worries, as I was saying"), then the held
        # clips carry on from the start of the sentence that was cut
        values = {v for f, v in zip(args, args[1:]) if f in ("--agent", "--session", "--voice")}
        words = [a for a in args if a not in commands and a not in values
                 and a not in ("--agent", "--session", "--voice")]
        lead = " ".join(words).strip() or ("" if sys.stdin.isatty() else sys.stdin.read().strip())
        mine = [item for item in clips() if read_json(item).get("session") == key]
        if lead and mine:
            enqueue(agent, key, lead, os.environ.get("TALKBACK_VOICE", ""),
                    at=int(mine[0].name.split("-")[0]) - 1)
        elif lead:
            enqueue(agent, key, lead, os.environ.get("TALKBACK_VOICE", ""))
        (HOLDS / key).unlink(missing_ok=True)
        spawn_worker()
        return 0
    if command == "--drop":
        drop(key)
        if read_json(PLAYING).get("session") == key:
            try:
                os.kill(int(PIDFILE.read_text().strip()), signal.SIGUSR1)
            except (FileNotFoundError, ValueError, ProcessLookupError):
                pass
        return 0
    words, skip = [], False
    for i, arg in enumerate(args):
        if skip:
            skip = False
        elif arg in ("--agent", "--session", "--voice"):
            skip = True
        elif arg != "--wait":
            words.append(arg)
    text = " ".join(words).strip() or sys.stdin.read().strip()
    if not text:
        return 0
    voice = next((v for f, v in zip(args, args[1:]) if f == "--voice"), "")
    if held(key):
        # Speaking again means the agent has moved on: what it had not said
        # before the user cut in is stale, and holding the new reply behind it
        # left the user in silence whenever an agent forgot --release/--drop
        drop(key)
        log(f"{agent} spoke again; dropped its older held clips")
    item = enqueue(agent, key, text, voice or os.environ.get("TALKBACK_VOICE", ""))
    spawn_worker()
    while "--wait" in args and (
        item.exists() or read_json(PLAYING).get("clip") == item.name
    ):
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
