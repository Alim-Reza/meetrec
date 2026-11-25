# Shared helpers for meetrec modules. Sourced, not executed.

STATE_DIR="${XDG_RUNTIME_DIR:-/tmp}/meetrec-$(id -u)"          # live recording state
TR_LOCK="${XDG_RUNTIME_DIR:-/tmp}/meetrec-$(id -u).transcribe.lock"  # one transcription at a time
RATE=48000

c_red=$'\e[31m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_dim=$'\e[2m'; c_off=$'\e[0m'
[[ -t 1 ]] || { c_red=; c_grn=; c_ylw=; c_dim=; c_off=; }

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✔%s %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s!%s %s\n' "$c_ylw" "$c_off" "$*" >&2; }
die()  { printf '%s✘%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }

notify() {
  command -v notify-send >/dev/null 2>&1 || return 0
  notify-send -a meetrec -i "${2:-audio-input-microphone}" "meetrec" "$1" 2>/dev/null || true
}

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found. $2"; }

human_dur() { local s=${1%.*}; printf '%02d:%02d:%02d' $((s/3600)) $((s%3600/60)) $((s%60)); }

# Resolve a recording folder argument: empty → latest, name → under MEETREC_DIR, path → as is.
resolve_dir() {
  local a="${1:-}"
  if [[ -z "$a" ]]; then
    a=$(ls -1d "$MEETREC_DIR"/*/ 2>/dev/null | sort | tail -1)
    [[ -n "$a" ]] || die "No recordings in $MEETREC_DIR"
  elif [[ ! -d "$a" && -d "$MEETREC_DIR/$a" ]]; then
    a="$MEETREC_DIR/$a"
  fi
  [[ -d "$a" ]] || die "Not a recording folder: $1"
  (cd "$a" && pwd)
}
