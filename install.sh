#!/usr/bin/env bash
# Installs meetrec for the current user. Everything lives in this folder; only a
# symlink, an app-menu launcher and (optionally) a keyboard shortcut go outside it.
#
#   ./install.sh               link CLI, set up transcriber (.venv + model), launcher
#   ./install.sh --deps        also apt-install system packages (asks for sudo)
#   ./install.sh --shortcut    also bind Super+Shift+R to "meetrec toggle" (GNOME)
#   ./install.sh --no-model    skip the model download (it happens on first transcription)
#   ./install.sh --uninstall   remove symlink/launcher/shortcut (keeps recordings & this folder)
set -euo pipefail

HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
KB_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/meetrec/"
KB_SCHEMA="org.gnome.settings-daemon.plugins.media-keys"

deps=0; shortcut=0; uninstall=0; model=1
for a in "$@"; do
  case "$a" in
    --deps) deps=1 ;; --shortcut) shortcut=1 ;; --uninstall) uninstall=1 ;; --no-model) model=0 ;;
    *) echo "unknown option: $a"; exit 1 ;;
  esac
done

kb_list() { gsettings get "$KB_SCHEMA" custom-keybindings 2>/dev/null || echo "@as []"; }
kb_edit() { # kb_edit add|remove
  python3 - "$1" "$(kb_list)" "$KB_PATH" <<'PY'
import ast, sys
op, cur, path = sys.argv[1:]
l = ast.literal_eval(cur.replace("@as ", ""))
if op == "add" and path not in l: l.append(path)
if op == "remove" and path in l: l.remove(path)
print(l)
PY
}

if (( uninstall )); then
  rm -f "$BIN/meetrec" "$APPS/meetrec-toggle.desktop"
  if command -v gsettings >/dev/null && [[ "$(kb_list)" == *"$KB_PATH"* ]]; then
    gsettings set "$KB_SCHEMA" custom-keybindings "$(kb_edit remove)"
    gsettings reset-recursively "$KB_SCHEMA.custom-keybinding:$KB_PATH" 2>/dev/null || true
  fi
  echo "✔ uninstalled. Recordings and $HERE were kept (delete .venv/ and models/ to free space)."
  exit 0
fi

# --- system packages ---
if (( deps )); then
  echo "→ Installing system packages (sudo)…"
  sudo apt-get update -qq
  sudo apt-get install -y ffmpeg pulseaudio-utils libnotify-bin pipewire-bin wireplumber python3-pip
fi

# --- CLI: symlink so edits in this folder take effect immediately ---
mkdir -p "$BIN" "$APPS"
source "$HERE/config.env"; mkdir -p "$MEETREC_DIR"
chmod +x "$HERE/meetrec" "$HERE/install.sh" "$HERE"/transcriber/transcribe_*.py
ln -sfn "$HERE/meetrec" "$BIN/meetrec"
echo "✔ $BIN/meetrec → $HERE/meetrec"

# --- transcriber: private venv in .venv ---
# Ubuntu doesn't ship python3-venv by default (needs sudo), so create the venv
# without pip and let the system pip install into it via --python.
VENV="$HERE/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "→ Creating Python environment in .venv"
  python3 -m venv --without-pip "$VENV"
fi
if ! "$VENV/bin/python" -c "import faster_whisper, elevenlabs, dotenv, jiwer" 2>/dev/null; then
  echo "→ Installing faster-whisper + ElevenLabs SDK into .venv (≈350 MB)…"
  if "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
    "$VENV/bin/python" -m pip install -q -r "$HERE/transcriber/requirements.txt"
  else
    pip3 --python "$VENV/bin/python" install -q --disable-pip-version-check -r "$HERE/transcriber/requirements.txt"
  fi
fi
echo "✔ transcriber environment ready"
[[ -f "$HERE/transcriber/vocab.txt" ]] || { cp "$HERE/transcriber/vocab.example.txt" "$HERE/transcriber/vocab.txt"; echo "✔ created transcriber/vocab.txt from the example — put your own names & terms in it"; }

if (( model )); then
  echo "→ Fetching Whisper model '$MEETREC_MODEL' into models/ (first time ≈3 GB)…"
  MEETREC_MODELS_DIR="$HERE/models" "$VENV/bin/python" "$HERE/transcriber/transcribe_local.py" --download
fi

# --- app-menu launcher ---
cat > "$APPS/meetrec-toggle.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Meeting Recorder (start/stop)
Comment=Toggle recording of all meeting audio (speakers + mic), transcribed afterwards
Exec=$BIN/meetrec toggle
Icon=media-record
Terminal=false
Categories=AudioVideo;Audio;Recorder;
Keywords=record;meeting;teams;zoom;audio;transcribe;
EOF
echo "✔ app-menu launcher: \"Meeting Recorder (start/stop)\""

# --- keyboard shortcut ---
if (( shortcut )); then
  if command -v gsettings >/dev/null; then
    gsettings set "$KB_SCHEMA" custom-keybindings "$(kb_edit add)"
    S="$KB_SCHEMA.custom-keybinding:$KB_PATH"
    gsettings set "$S" name "Meeting recorder toggle"
    gsettings set "$S" command "$BIN/meetrec toggle"
    gsettings set "$S" binding "<Super><Shift>r"
    echo "✔ shortcut: Super+Shift+R → meetrec toggle"
  else
    echo "! gsettings not found; skipping shortcut"
  fi
fi

echo
"$BIN/meetrec" doctor || true
