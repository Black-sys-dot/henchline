#!/bin/sh
# Install or remove the pieces, each on its own:
#   ./install.sh ptt|talkback|handsfree|call|agents|all ...
#   ./install.sh remove ptt|talkback|handsfree|call|agents|all ...
# Python packages go into one private venv (~/.local/share/copilot-ptt/venv);
# nothing is installed into the system or the user's own Python. Removing a
# piece leaves the venv, voice models and wake-word recordings in place.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
share="$HOME/.local/share"
venv="$share/copilot-ptt/venv"
bin="$HOME/.local/bin"
units="$HOME/.config/systemd/user"
kokoro="$share/talkback"  # Kokoro model files (the worker reads them here)
kokoro_url="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
apt_dir=/usr/lib/python3/dist-packages
snippet_name="agents/AGENTS-snippet.md"  # how ~/.claude/CLAUDE.md imports the agent rules

say() { printf '%s\n' "$*"; }

# --- the private venv -------------------------------------------------------

venv_python() { env -u PYTHONPATH "$venv/bin/python3" -s "$@"; }

make_venv() {
  if [ ! -x "$venv/bin/pip" ]; then  # pip last in: without it the venv is half made
    say "Creating the venv in $venv"
    rm -rf "$venv"
    env -u PYTHONPATH /usr/bin/python3 -m venv "$venv" || { rm -rf "$venv"; say "python3 -m venv failed: sudo apt install python3-venv"; exit 1; }
    venv_python -m pip install -q --upgrade pip
  fi
  # Only PyQt5 and evdev come from apt: the whole system site would bring
  # packages built for another numpy (an apt pandas crashed scikit-learn)
  mkdir -p "$venv/system"
  for path in "$apt_dir/PyQt5" "$apt_dir/evdev" "$apt_dir"/sip.cpython-*.so; do
    if [ -e "$path" ]; then ln -sfn "$path" "$venv/system/${path##*/}"; fi
  done
  site=$(venv_python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
  echo "$venv/system" > "$site/apt-packages.pth"
}

need_apt() {
  for name in PyQt5 evdev; do
    if [ ! -e "$venv/system/$name" ]; then
      say "Missing from apt: $name. Run: sudo apt install python3-venv python3-pyqt5 python3-evdev curl"
      exit 1
    fi
  done
}

pip_install() {  # requirement files, by name
  for name in "$@"; do set -- "$@" -r "$root/requirements/$name.txt"; shift; done
  venv_python -m pip install -q "$@"
}

start_or_restart() {  # a rerun brings running units onto the new code, interpreter and settings
  for unit in "$@"; do
    if systemctl --user is-active --quiet "$unit"; then systemctl --user restart "$unit"; fi
  done
}

with_gpu() {  # the CUDA libraries, only with an NVIDIA GPU
  if command -v nvidia-smi >/dev/null; then pip_install cuda; fi
}

# --- the pieces --------------------------------------------------------------

install_ptt() {
  make_venv
  need_apt
  pip_install ptt
  with_gpu
  id -nG | grep -qw input || say "Note: the hotkey needs the input group: sudo usermod -aG input $(id -un) (then log in again)"
  mkdir -p "$bin" "$units" "$HOME/.config/ptt"
  ln -sfn "$root/ptt/ptt.py" "$bin/ptt"
  install -m 644 "$root/ptt/ptt.service" "$units/ptt.service"
  [ -f "$HOME/.config/ptt/config.json" ] || install -m 644 "$root/ptt/config.example.json" "$HOME/.config/ptt/config.json"
  systemctl --user daemon-reload
  systemctl --user enable ptt.service
  systemctl --user restart ptt.service  # starts it, or brings a running one onto the new code
  say "Push-to-talk: on, and at every login. Hold the Copilot key (F23) and speak."
}

talkback_done=""
install_talkback() {  # hands-free and the agent rules need it too: once is enough
  [ -z "$talkback_done" ] || return 0
  talkback_done=1
  make_venv
  pip_install talkback
  mkdir -p "$kokoro" "$bin"
  for file in kokoro-v1.0.onnx voices-v1.0.bin; do
    if [ ! -s "$kokoro/$file" ]; then
      say "Downloading $file"
      curl -fL --progress-bar -o "$kokoro/$file.part" "$kokoro_url/$file"  # never a half file under the real name
      mv "$kokoro/$file.part" "$kokoro/$file"
    fi
  done
  ln -sfn "$root/talkback/scripts/say.py" "$bin/talkback"
  say "Talkback: the talkback command speaks. ./install.sh agents tells your agents about it."
}

builtin_mic() {
  # A mic on the same sound card as the speakers: same clock, so the canceller
  # lines up (a USB webcam mic drifted against them). The default output's card
  # first; with earbuds as the default, the first card that has both.
  card=$(pactl get-default-sink 2>/dev/null | sed -n 's/^alsa_output\.\(.*\)\.[^.]*$/\1/p')
  outputs=$(pactl list short sinks | awk '{print $2}' | sed -n 's/^alsa_output\.\(.*\)\.[^.]*$/\1/p')
  for card in $card $outputs; do
    mic=$(pactl list short sources | awk '{print $2}' | grep "^alsa_input\.$card\." | grep -v monitor | head -n 1)
    if [ -n "$mic" ]; then printf '%s\n' "$mic"; return 0; fi
  done
}

handsfree_done=""
install_handsfree() {  # the call needs it too: once is enough
  [ -z "$handsfree_done" ] || return 0
  handsfree_done=1
  install_talkback
  need_apt
  pip_install ptt handsfree
  with_gpu
  venv_python -m pip install -q --no-deps openwakeword==0.6.0  # its tflite dependency has no Python 3.12 build
  venv_python -c 'from openwakeword.utils import download_models; download_models(model_names=["none"])' >/dev/null 2>&1 ||
    { say "Wake word feature models: download failed (offline?). Run ./install.sh handsfree again later."; exit 1; }
  # "hey Jarvis": openWakeWord's published model, a wake word that needs no recordings of your voice
  venv_python -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'], '$share/handsfree')" \
    >/dev/null 2>&1 || say "The hey Jarvis model did not download (offline?): hey Claude still works once trained."
  mic=$(builtin_mic || true)
  mkdir -p "$units" "$HOME/.config/pipewire" "$bin"
  conf="$HOME/.config/pipewire/talkback-echo-cancel.conf"
  if [ -n "$mic" ]; then
    sed "s|@MIC@|$mic|" "$root/handsfree/talkback-echo-cancel.conf" > "$conf"
    say "Echo canceller listens to $mic"
  else
    grep -v "@MIC@" "$root/handsfree/talkback-echo-cancel.conf" > "$conf"
    say "Echo canceller listens to the default mic: none found on the output's sound card"
  fi
  install -m 644 "$root/handsfree/talkback-echo-cancel.service" "$units/talkback-echo-cancel.service"
  install -m 644 "$root/handsfree/handsfree.service" "$units/handsfree.service"
  ln -sfn "$root/handsfree/handsfree.py" "$bin/handsfree"
  systemctl --user daemon-reload
  start_or_restart talkback-echo-cancel.service handsfree.service
  say "Hands-free: handsfree on (then it starts again at login)."
  [ -f "$share/handsfree/hey_jarvis_v0.1.onnx" ] && say "\"Hey Jarvis\" wakes it."
  say "\"Hey Claude\" needs your voice first:"
  say "  handsfree/wakeword_collect.py, then handsfree/wakeword_train.py"
}

install_call() {
  install_handsfree  # a call is hands-free with the phone as mic and speaker
  pip_install call
  for tool in tailscale openssl; do
    command -v "$tool" >/dev/null || { say "The call page needs $tool"; exit 1; }
  done
  tls="$share/copilot-ptt/call"
  if [ ! -s "$tls/cert.pem" ]; then  # its own certificate; the phone accepts it once
    mkdir -p "$tls"
    ip=$(tailscale ip -4 | head -n 1)
    name=$(tailscale status --json | venv_python -c 'import json, sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
    openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout "$tls/key.pem" -out "$tls/cert.pem" \
      -subj "/CN=$name" -addext "subjectAltName=IP:$ip,DNS:$name" 2>/dev/null
    chmod 600 "$tls/key.pem"
  fi
  mkdir -p "$bin" "$units"
  ln -sfn "$root/call/server.py" "$bin/voice-call"
  install -m 644 "$root/call/call.service" "$units/call.service"
  systemctl --user daemon-reload
  systemctl --user enable call.service
  systemctl --user restart call.service
  # 8790 is voice/state.py's CALL_PORT (a shell script cannot read it)
  if tailscale serve status 2>/dev/null | grep -q ":8790"; then
    say "Call page: https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')/ on your phone, then Call."
  else
    say "Call page: https://$(tailscale ip -4 | head -n 1):8790 on your phone (accept the certificate once), then Call."
    say "For a proper address and certificate: sudo tailscale set --operator=\$USER, then systemctl --user restart call"
  fi
}

install_agents() {
  install_talkback
  ln -sfn "$root/talkback" "$share/talkback/skill"
  snippet="$root/$snippet_name"
  if [ -d "$HOME/.claude" ]; then
    mkdir -p "$HOME/.claude/skills"
    ln -sfn "$root/talkback" "$HOME/.claude/skills/talkback"
    case "$snippet" in "$HOME"/*) import="~/${snippet#"$HOME"/}" ;; *) import="$snippet" ;; esac
    grep -qF "$snippet_name" "$HOME/.claude/CLAUDE.md" 2>/dev/null || printf '\n@%s\n' "$import" >> "$HOME/.claude/CLAUDE.md"
    say "Claude Code: imports the snippet from ~/.claude/CLAUDE.md"
  fi
  if [ -d "$HOME/.cursor" ]; then
    mkdir -p "$HOME/.cursor/rules" "$HOME/.cursor/skills"
    # The Cursor rule is the same snippet under Cursor's header, made here so there is one copy of the text
    { printf -- '---\ndescription: Talkback, tone marks, hands-free messages, calling the user and files (from copilot-ptt/agents/AGENTS-snippet.md)\nalwaysApply: true\n---\n\n'
      cat "$snippet"; } > "$HOME/.cursor/rules/voice.mdc"
    ln -sfn "$root/talkback" "$HOME/.cursor/skills/talkback"
    keys="$HOME/.config/Cursor/User/keybindings.json"
    if [ -f "$keys" ] && ! grep -q "composer.focusComposer" "$keys"; then
      python3 - "$keys" <<'PYEOF'
import sys
path = sys.argv[1]
text = open(path).read()
entry = '    {\n        "key": "ctrl+alt+shift+f12",\n        "command": "composer.focusComposer"\n    },\n'
i = text.index("[") + 1
open(path, "w").write(text[:i] + "\n" + entry + text[i:].lstrip("\n"))
PYEOF
    fi
    say "Cursor: rule ~/.cursor/rules/voice.mdc, chat-focus key ctrl+alt+shift+f12"
  fi
  if [ -d "$HOME/.codex" ]; then
    if [ -e "$HOME/.codex/AGENTS.md" ] && [ ! -L "$HOME/.codex/AGENTS.md" ]; then
      grep -qF "## Talkback" "$HOME/.codex/AGENTS.md" || cat "$snippet" >> "$HOME/.codex/AGENTS.md"
    else
      ln -sfn "$snippet" "$HOME/.codex/AGENTS.md"
    fi
    say "Codex: ~/.codex/AGENTS.md"
  fi
}

# --- removal: what install put in place, nothing else -------------------------

remove_unit() {
  systemctl --user disable --now "$1" 2>/dev/null || true
  rm -f "$units/$1"
}

remove_link() {  # only if it still points into this repository
  case "$(readlink "$1" 2>/dev/null || true)" in "$root"/*) rm -f "$1" ;; esac
}

remove_ptt() {
  remove_unit ptt.service
  remove_link "$bin/ptt"
  systemctl --user daemon-reload
  say "Push-to-talk removed (settings kept in ~/.config/ptt)"
}

remove_talkback() {
  remove_link "$bin/talkback"
  say "Talkback removed (Kokoro models kept in $kokoro)"
}

remove_handsfree() {
  "$bin/handsfree" off 2>/dev/null || true
  remove_unit handsfree.service
  remove_unit talkback-echo-cancel.service
  rm -f "$HOME/.config/pipewire/talkback-echo-cancel.conf"
  remove_link "$bin/handsfree"
  systemctl --user daemon-reload
  say "Hands-free removed (wake word model and recordings kept in $share/handsfree)"
}

remove_call() {
  remove_unit call.service
  remove_link "$bin/voice-call"
  systemctl --user daemon-reload
  say "Call page removed (its certificate kept in $share/copilot-ptt/call)"
}

remove_agents() {
  remove_link "$share/talkback/skill"
  remove_link "$HOME/.claude/skills/talkback"
  remove_link "$HOME/.cursor/skills/talkback"
  remove_link "$HOME/.codex/AGENTS.md"
  rm -f "$HOME/.cursor/rules/voice.mdc"
  if [ -f "$HOME/.claude/CLAUDE.md" ]; then
    # without the import line, and without the blank line left before it at the end
    kept=$(sed "\|^@.*/$snippet_name\$|d" "$HOME/.claude/CLAUDE.md")
    printf '%s\n' "$kept" > "$HOME/.claude/CLAUDE.md"
  fi
  say "Agent rules removed (the Cursor keybinding stays; on its own it does nothing)"
}

# --- arguments ---------------------------------------------------------------

action=install
if [ "${1:-}" = remove ]; then action=remove; shift; fi
if [ $# -eq 0 ]; then sed -n '2,7p' "$0"; exit 1; fi
for piece in "$@"; do
  case "$piece" in
    all) set -- ptt talkback handsfree agents; break ;;  # the call needs Tailscale: ./install.sh call
    ptt|talkback|handsfree|call|agents) ;;
    *) say "Unknown piece: $piece"; exit 1 ;;
  esac
done
for piece in "$@"; do "${action}_$piece"; done
if [ "$action" = install ]; then say "Check anything that is still missing: $root/doctor.py $*"; fi
