#!/usr/bin/env python3
"""
Local engine: transcribe a meetrec recording with faster-whisper (offline, on this laptop).

    transcribe_local.py <recording-folder>   → transcript_local.md / .srt / .json
    transcribe_local.py --download           → just fetch the model (used by install.sh)

Speaker labels come for free: me.opus is "Me", others.opus is "Them". Each track is
transcribed separately and the segments are merged by time.

Settings (env): MEETREC_MODEL, MEETREC_LANG ("auto" = detect), MEETREC_THREADS, MEETREC_MODELS_DIR.
"""
import os
import re
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

from tx_common import HOME, load_vocab, log, write_outputs

MODEL = os.environ.get("MEETREC_MODEL") or "large-v3"
LANG = os.environ.get("MEETREC_LANG") or "en"
LANG = None if LANG == "auto" else LANG
THREADS = int(os.environ.get("MEETREC_THREADS") or 0) or max(4, (os.cpu_count() or 8) // 2)
MODELS_DIR = os.environ.get("MEETREC_MODELS_DIR") or str(HOME / "models")
USE_VOCAB = (os.environ.get("MEETREC_LOCAL_VOCAB") or "1") == "1"
# Detect the language per segment, so a Bengali sentence in an English meeting is written as
# Bengali instead of being forced into (made-up) English.
MULTILINGUAL = (os.environ.get("MEETREC_LOCAL_MULTILINGUAL") or "0") == "1"

# vocab.txt → Whisper "hotwords": prepended as context to EVERY 30 s window (unlike
# initial_prompt, which only reaches the first window when condition_on_previous_text is
# off). Written as a punctuated sentence, it also nudges Whisper back to proper casing and
# punctuation instead of a lowercase run-on.
VOCAB = load_vocab() if USE_VOCAB else []
HOTWORDS = ("Meeting notes. Glossary: " + ", ".join(VOCAB) + ".") if VOCAB else None

TRACKS = [("Me", "me.opus"), ("Them", "others.opus")]
FALLBACK = ("Speaker", "meeting.opus")


def load_model():
    from faster_whisper import WhisperModel
    t = time.time()
    log(f"Loading model {MODEL} (int8, {THREADS} threads)…")
    m = WhisperModel(MODEL, device="cpu", compute_type="int8",
                     cpu_threads=THREADS, download_root=MODELS_DIR)
    log(f"  ready in {time.time() - t:.1f}s")
    return m


def transcribe_track(model, path, label):
    segments, info = model.transcribe(
        str(path),
        language=None if MULTILINGUAL else LANG,
        multilingual=MULTILINGUAL,
        beam_size=5,
        vad_filter=True,                       # skip silence: faster + no "thanks for watching" ghosts
        vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 200},
        condition_on_previous_text=False,      # avoids repetition loops on long audio
        hotwords=HOTWORDS,
    )
    out, last_pct, tty = [], -10, sys.stdout.isatty()
    for s in segments:
        text = s.text.strip()
        if not text or (s.no_speech_prob > 0.6 and s.avg_logprob < -1.0):
            continue
        out.append({"speaker": label, "start": round(s.start, 2), "end": round(s.end, 2), "text": text})
        pct = 100 * s.end / info.duration if info.duration else 100
        if tty:
            print(f"\r  {label:<5} {pct:5.1f}%", end="", flush=True)
        elif pct - last_pct >= 10:
            log(f"  {label:<5} {pct:5.1f}%")
            last_pct = pct
    if tty:
        print(f"\r  {label:<5} 100.0%", flush=True)
    return out, info


def _norm(t):
    return re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()


def drop_echo(me, them):
    """Remove 'Me' segments that are just the other side leaking from speakers into the mic."""
    kept = []
    for m in me:
        mt = _norm(m["text"])
        near = " ".join(_norm(t["text"]) for t in them
                        if t["start"] <= m["end"] + 1.5 and t["end"] >= m["start"] - 1.5)
        if mt and near:
            matched = sum(b.size for b in SequenceMatcher(None, mt, near, autojunk=False).get_matching_blocks())
            if matched / len(mt) > 0.6:
                continue
        kept.append(m)
    return kept, len(me) - len(kept)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--download":
        load_model()
        return

    folder = Path(sys.argv[1]).resolve()
    tracks = [(lbl, folder / f) for lbl, f in TRACKS if (folder / f).exists()]
    if not tracks:
        if not (folder / FALLBACK[1]).exists():
            sys.exit(f"No audio found in {folder}")
        tracks = [(FALLBACK[0], folder / FALLBACK[1])]

    t0 = time.time()
    model = load_model()
    if VOCAB:
        log(f"Using {len(VOCAB)} vocab terms as hotwords")
    by_label, duration, lang_seen = {}, 0.0, set()
    for label, path in tracks:
        log(f"Transcribing {path.name} as “{label}”…")
        segs, info = transcribe_track(model, path, label)
        by_label[label] = segs
        duration = max(duration, info.duration)
        lang_seen.add(info.language)
        log(f"  {len(segs)} segments")

    echo_dropped = 0
    if "Me" in by_label and "Them" in by_label:
        by_label["Me"], echo_dropped = drop_echo(by_label["Me"], by_label["Them"])
        if echo_dropped:
            log(f"Removed {echo_dropped} echo segment(s) from the mic track")

    segs = [s for v in by_label.values() for s in v]
    elapsed = time.time() - t0
    md = write_outputs(folder, "local", segs, duration, f"local Whisper {MODEL}",
                       ",".join(sorted(lang_seen)), elapsed,
                       {"model": MODEL, "vocab_terms": len(VOCAB), "multilingual": MULTILINGUAL, "echo_segments_removed": echo_dropped})
    log(f"Done: {len(segs)} segments, {duration:.0f}s of audio in {elapsed:.0f}s "
        f"({duration / max(elapsed, 0.1):.1f}× real-time)")
    log(f"  {md}")


if __name__ == "__main__":
    main()
