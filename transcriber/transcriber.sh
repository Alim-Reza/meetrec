# transcriber: runs the transcription engines on a recording folder. Sourced by ../meetrec.
#
# Engines (MEETREC_ENGINES in config.env, run in this order):
#   11     ElevenLabs Scribe (cloud)       → transcript_11.md / .srt / .json   log: transcribe_11.log
#   local  faster-whisper (this laptop)    → transcript_local.md / .srt / .json log: transcribe_local.log
#
# Folder markers:  .transcribe-queued  → waiting for the lock
#                  .transcribing       → in progress

VENV_PY="$MEETREC_HOME/.venv/bin/python"
export MEETREC_MODELS_DIR="${MEETREC_MODELS_DIR:-$MEETREC_HOME/models}"

engine_script() { echo "$MEETREC_HOME/transcriber/transcribe_$1.py"; }
engine_name()   { case "$1" in 11) echo "ElevenLabs" ;; local) echo "local Whisper" ;; *) echo "$1" ;; esac; }

transcriber_ready() { [[ -x "$VENV_PY" ]] && "$VENV_PY" -c "import faster_whisper" 2>/dev/null; }

valid_engine() { [[ -f "$(engine_script "$1")" ]] || die "Unknown engine '$1' (available: 11, local)"; }

# run_engine <engine> <dir> [fg]  — fg: also show output on the terminal
run_engine() {
  local e="$1" d="$2" fg="${3:-}" log="$2/transcribe_$1.log" rc=0
  local cmd=("$VENV_PY" "$(engine_script "$e")" "$d")
  [[ "$e" == local ]] && cmd=(nice -n 15 ionice -c 3 "${cmd[@]}")   # CPU-heavy: stay out of the way
  if [[ -n "$fg" ]]; then "${cmd[@]}" 2>&1 | tee "$log" || rc=$?
  else "${cmd[@]}" > "$log" 2>&1 || rc=$?; fi
  return $rc
}

# Queue a folder for background transcription and return immediately.
transcriber_enqueue() {
  local d="$1"
  transcriber_ready || { warn "Transcriber not installed — run ./install.sh in $MEETREC_HOME"; return 0; }
  touch "$d/.transcribe-queued"
  setsid nohup "$MEETREC_HOME/meetrec" _transcribe-worker "$d" >/dev/null 2>&1 < /dev/null &
  say "${c_dim}📝 Transcription queued in background: $MEETREC_ENGINES (meetrec status to check)${c_off}"
}

# Background worker: waits its turn, runs every engine, notifies after each.
transcriber_worker() {
  local d="$1" name e; name=$(basename "$d")
  exec 9>"$TR_LOCK"; flock 9
  rm -f "$d/.transcribe-queued"; touch "$d/.transcribing"
  for e in $MEETREC_ENGINES; do
    if run_engine "$e" "$d"; then
      notify "📝 $(engine_name "$e") transcript ready → $name" accessories-text-editor
    else
      notify "⚠ $(engine_name "$e") transcription failed for $name (see transcribe_$e.log)" dialog-warning
    fi
  done
  rm -f "$d/.transcribing"
  # benchmark local against ElevenLabs → compare.txt + a line in benchmarks.tsv
  if [[ -f "$d/transcript_11.json" && -f "$d/transcript_local.json" ]]; then
    "$VENV_PY" "$MEETREC_HOME/transcriber/compare.py" "$d" --log > "$d/compare.txt" 2>&1 || true
  fi
}

# meetrec compare [rec … | --all] [--log]
cmd_compare() {
  transcriber_ready || die "Transcriber not installed — run ./install.sh in $MEETREC_HOME"
  local dirs=() log="" a d
  for a in "$@"; do
    case "$a" in
      --log) log="--log" ;;
      --all) for d in "$MEETREC_DIR"/*/; do
               [[ -f "$d/transcript_11.json" && -f "$d/transcript_local.json" ]] && dirs+=("${d%/}")
             done ;;
      *) dirs+=("$(resolve_dir "$a")") ;;
    esac
  done
  (( ${#dirs[@]} )) || dirs=("$(resolve_dir "")")
  "$VENV_PY" "$MEETREC_HOME/transcriber/compare.py" "${dirs[@]}" $log
  if [[ -f "$MEETREC_HOME/benchmarks.tsv" ]]; then
    say "${c_dim}History ($MEETREC_HOME/benchmarks.tsv, last 10):${c_off}"
    { head -1 "$MEETREC_HOME/benchmarks.tsv"; tail -n +2 "$MEETREC_HOME/benchmarks.tsv" | tail -10; } | column -t -s $'\t' | sed 's/^/  /'
  fi
}

# Foreground: meetrec transcribe [folder | --missing] [--engine 11|local]
cmd_transcribe() {
  transcriber_ready || die "Transcriber not installed — run ./install.sh in $MEETREC_HOME"
  local engines="$MEETREC_ENGINES" target="" missing=0
  while (( $# )); do
    case "$1" in
      --engine|-e) shift; engines="${1:?--engine needs 11 or local}" ;;
      --missing)   missing=1 ;;
      *)           target="$1" ;;
    esac
    shift
  done
  local e; for e in $engines; do valid_engine "$e"; done

  local dirs=() d
  if (( missing )); then
    for d in "$MEETREC_DIR"/*/; do
      d="${d%/}"
      [[ -f "$d/meeting.opus" && ! -f "$d/.transcribing" ]] || continue
      for e in $engines; do [[ -f "$d/transcript_$e.md" ]] || { dirs+=("$d"); break; }; done
    done
    (( ${#dirs[@]} )) || { say "Every recording already has a transcript ($engines)."; return 0; }
  else
    dirs=("$(resolve_dir "$target")")
  fi

  for d in "${dirs[@]}"; do
    exec 9>"$TR_LOCK"
    flock -n 9 || { say "${c_dim}…waiting for a background transcription to finish${c_off}"; flock 9; }
    rm -f "$d/.transcribe-queued"; touch "$d/.transcribing"
    for e in $engines; do
      # with --missing, only run the engines whose transcript is absent
      (( missing )) && [[ -f "$d/transcript_$e.md" ]] && continue
      say "── $(engine_name "$e") · $(basename "$d")"
      if run_engine "$e" "$d" fg; then ok "$d/transcript_$e.md"
      else warn "$(engine_name "$e") failed (see $d/transcribe_$e.log)"; fi
    done
    rm -f "$d/.transcribing"; exec 9>&-
  done
}

transcriber_status() {
  local d e found=0
  for d in "$MEETREC_DIR"/*/; do
    d="${d%/}"
    if [[ -f "$d/.transcribing" ]]; then
      found=1; say "📝 transcribing: $(basename "$d")"
      for e in $MEETREC_ENGINES; do
        local st
        if [[ -f "$d/transcript_$e.md" ]]; then st="✔ done"
        elif [[ -f "$d/transcribe_$e.log" ]]; then
          st="… $(tail -c 300 "$d/transcribe_$e.log" | tr '\r' '\n' | grep -v '^\s*$' | tail -1)"
        else st="waiting"; fi
        say "     $(printf '%-14s' "$(engine_name "$e")") ${c_dim}$st${c_off}"
      done
    elif [[ -f "$d/.transcribe-queued" ]]; then
      found=1; say "⏳ queued:       $(basename "$d")"
    fi
  done
  (( found )) || say "${c_dim}No transcriptions running.${c_off}"
}
