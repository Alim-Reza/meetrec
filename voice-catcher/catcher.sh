# voice-catcher: records the default output's monitor ("others") and the default mic ("me")
# with pw-record, then mixes/encodes to Opus with ffmpeg. Sourced by ../meetrec.

is_running() {
  [[ -f "$STATE_DIR/session" ]] || return 1
  local p
  for p in "$STATE_DIR"/pid.*; do
    [[ -f "$p" ]] && kill -0 "$(cat "$p")" 2>/dev/null && return 0
  done
  return 1
}

# Seconds of audio in a raw s16 file: bytes / (rate * channels * 2)
raw_secs() { local b; b=$(stat -c %s "$1" 2>/dev/null || echo 0); echo $(( b / (RATE * $2 * 2) )); }

default_name() { # pretty name of default sink/source
  wpctl inspect "$1" 2>/dev/null | sed -n 's/.*node.description = "\(.*\)"/\1/p' | head -1
}

# encode_session <dir>: others.raw + me.raw → meeting.opus, others.opus, me.opus (raw removed on success)
encode_session() {
  local d="$1"
  command -v ffmpeg >/dev/null 2>&1 || { warn "ffmpeg not found (sudo apt install ffmpeg)"; return 1; }
  local others="$d/others.raw" me="$d/me.raw"
  local in_o=(-f s16le -ar $RATE -ac 2 -i "$others")
  local in_m=(-f s16le -ar $RATE -ac 1 -i "$me")
  local ff=(ffmpeg -hide_banner -loglevel error -y)
  local opus=(-c:a libopus -application voip)
  local have_o=0 have_m=0
  [[ -s "$others" ]] && have_o=1
  [[ -s "$me" ]] && have_m=1
  (( have_o || have_m )) || { warn "No audio captured in $d"; return 1; }

  say "${c_dim}Encoding…${c_off}"
  if (( have_o && have_m )); then
    # mic copied to both channels, summed without auto-normalize, limiter against clipping
    "${ff[@]}" "${in_o[@]}" "${in_m[@]}" \
      -filter_complex "[1:a]pan=stereo|c0=c0|c1=c0[m];[0:a][m]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.95[out]" \
      -map "[out]" "${opus[@]}" -b:a "$MEETREC_BR_MIX" "$d/meeting.opus" || return 1
  elif (( have_o )); then
    "${ff[@]}" "${in_o[@]}" "${opus[@]}" -b:a "$MEETREC_BR_MIX" "$d/meeting.opus" || return 1
  else
    "${ff[@]}" "${in_m[@]}" "${opus[@]}" -b:a "$MEETREC_BR_MIX" "$d/meeting.opus" || return 1
  fi
  if (( have_o )); then "${ff[@]}" "${in_o[@]}" "${opus[@]}" -b:a "$MEETREC_BR_OTHERS" "$d/others.opus" || return 1; fi
  if (( have_m )); then "${ff[@]}" "${in_m[@]}" "${opus[@]}" -b:a "$MEETREC_BR_ME" "$d/me.opus" || return 1; fi
  rm -f "$others" "$me"
}

catcher_start() {
  need pw-record "It ships with PipeWire (package: pipewire-bin)."
  is_running && die "Already recording → $(cat "$STATE_DIR/session"). Use 'meetrec stop'."
  rm -rf "$STATE_DIR"; mkdir -p "$STATE_DIR"

  local name="${*:-}"
  local slug; slug=$(printf '%s' "$name" | tr -cs '[:alnum:]_-' '-' | sed 's/^-*//; s/-*$//')
  local dir="$MEETREC_DIR/$(date +%Y-%m-%d_%H-%M-%S)${slug:+_$slug}"
  mkdir -p "$dir"
  echo "$dir" > "$STATE_DIR/session"
  date +%s > "$STATE_DIR/started"

  # No --target: WirePlumber links us to the *default* devices and re-links us if you
  # switch (speakers → Bluetooth headset). stream.capture.sink=true = record the monitor.
  setsid pw-record -P '{ stream.capture.sink = true, node.name = "meetrec-others", media.name = "meetrec: others" }' \
    --media-category Capture --media-role Communication \
    --rate $RATE --channels 2 --format s16 - > "$dir/others.raw" 2>"$STATE_DIR/others.log" &
  echo $! > "$STATE_DIR/pid.others"

  setsid pw-record -P '{ node.name = "meetrec-me", media.name = "meetrec: me" }' \
    --media-category Capture --media-role Communication \
    --rate $RATE --channels 1 --format s16 - > "$dir/me.raw" 2>"$STATE_DIR/me.log" &
  echo $! > "$STATE_DIR/pid.me"

  sleep 0.7
  is_running || { cat "$STATE_DIR"/*.log >&2 2>/dev/null; die "Recorder failed to start."; }

  ok "Recording started"
  say "   output: ${c_dim}$(default_name @DEFAULT_AUDIO_SINK@)${c_off}"
  say "   mic:    ${c_dim}$(default_name @DEFAULT_AUDIO_SOURCE@)${c_off}"
  say "   saving: ${c_dim}$dir${c_off}"
  notify "🔴 Recording started${name:+: $name}" media-record
}

catcher_stop() {
  [[ -f "$STATE_DIR/session" ]] || die "Not recording."
  local dir; dir=$(cat "$STATE_DIR/session")
  local p
  for p in "$STATE_DIR"/pid.*; do kill -INT "$(cat "$p")" 2>/dev/null || true; done
  for _ in $(seq 50); do is_running || break; sleep 0.1; done
  for p in "$STATE_DIR"/pid.*; do kill -KILL "$(cat "$p")" 2>/dev/null || true; done

  local secs; secs=$(raw_secs "$dir/others.raw" 2)
  rm -rf "$STATE_DIR"
  ok "Recording stopped ($(human_dur "$secs"))"

  if encode_session "$dir"; then
    ok "Saved:"
    ls -1sh "$dir" | tail -n +2 | sed 's/^/   /'
    say "   ${c_dim}$dir${c_off}"
    notify "✅ Saved $(human_dur "$secs") → $(basename "$dir")" document-save
    [[ "$MEETREC_AUTO_TRANSCRIBE" == 1 ]] && transcriber_enqueue "$dir"
    return 0
  else
    warn "Encoding failed; raw audio kept in $dir — retry with 'meetrec recover'."
    notify "⚠ Encoding failed — raw audio kept" dialog-warning
    return 1
  fi
}

catcher_status() {
  if is_running; then
    local dir started; dir=$(cat "$STATE_DIR/session"); started=$(cat "$STATE_DIR/started")
    ok "Recording for $(human_dur $(( $(date +%s) - started )))"
    say "   saving: $dir"
    say "   others: $(du -h "$dir/others.raw" | cut -f1)   me: $(du -h "$dir/me.raw" | cut -f1)  (raw, compressed on stop)"
  else
    say "Not recording."
    [[ -f "$STATE_DIR/session" ]] && warn "Stale state found (recorder died?). Run 'meetrec stop' to finalize."
  fi
  return 0
}

catcher_recover() {
  is_running && die "Stop the current recording first."
  local d n=0
  for d in "$MEETREC_DIR"/*/; do
    [[ -f "$d/others.raw" || -f "$d/me.raw" ]] || continue
    d="${d%/}"; n=$((n+1))
    say "Recovering $(basename "$d")"
    if encode_session "$d"; then
      ok "done"
      [[ "$MEETREC_AUTO_TRANSCRIBE" == 1 ]] && transcriber_enqueue "$d"
    else
      warn "failed: $d"
    fi
  done
  (( n )) || say "Nothing to recover."
  rm -rf "$STATE_DIR"
}
