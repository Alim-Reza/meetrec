#!/usr/bin/env python3
"""
ElevenLabs engine: transcribe a meetrec recording with ElevenLabs Scribe (cloud API).

    transcribe_11.py <recording-folder>   → transcript_11.md / .srt / .json (+ transcript_11.raw.json)

Cost: only meeting.opus (the mix) is uploaded, with diarization on, so each meeting is billed
once for its length. Splitting by speaker is done by ElevenLabs; which speaker is *you* is
worked out locally by checking which diarized speaker lines up with speech on me.opus.

Vocabulary from transcriber/vocab.txt is sent as keyterms (max 100).

The API key is read from ELEVENLABS_API_KEY, loaded from ../.env.local (written by
`npx @elevenlabs/cli onboard`). It never appears in this code.

Settings (env): MEETREC_11_MODEL (default scribe_v3), MEETREC_LANG ("auto" = detect).
"""
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from tx_common import HOME, load_vocab, log, write_atomic, write_outputs

load_dotenv(HOME / ".env.local", override=False)

MODEL = os.environ.get("MEETREC_11_MODEL") or "scribe_v3"
LANG = os.environ.get("MEETREC_LANG") or "en"
LANG = None if LANG == "auto" else LANG
# Other languages that may be mixed in, e.g. "bn" (opt-in: Scribe usually switches on its own)
SECONDARY = (os.environ.get("MEETREC_11_SECONDARY_LANGS") or "").split()
MAX_KEYTERMS = 100
SEG_GAP = 1.5        # new segment after a pause this long (s)
SEG_MAX = 25.0       # … or after this long, at a sentence end


# ---------- which diarized speaker is "Me"? ----------

def speech_intervals(path):
    """Speech regions (seconds) in an audio file, via faster-whisper's Silero VAD (local)."""
    from faster_whisper.audio import decode_audio
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    audio = decode_audio(str(path), sampling_rate=16000)
    ts = get_speech_timestamps(audio, VadOptions(min_silence_duration_ms=300, speech_pad_ms=100))
    return [(t["start"] / 16000, t["end"] / 16000) for t in ts]


def overlap(a0, a1, intervals):
    return sum(max(0.0, min(a1, b1) - max(a0, b0)) for b0, b1 in intervals)


def find_me(words, folder):
    """Return the set of speaker_ids that are you.

    For each diarized speaker, measure how much of their speaking time overlaps speech on
    me.opus versus others.opus. With a headset your voice is only on me.opus; the other
    side is only on others.opus (and may leak into me.opus via speakers, which is why we
    compare against others.opus instead of using me.opus alone).
    """
    me_f, oth_f = folder / "me.opus", folder / "others.opus"
    if not me_f.exists():
        return set(), {}
    me_iv = speech_intervals(me_f)
    oth_iv = speech_intervals(oth_f) if oth_f.exists() else []
    stats = {}
    for w in words:
        if w["type"] != "word" or not w.get("speaker_id"):
            continue
        d = max(w["end"] - w["start"], 0.01)
        s = stats.setdefault(w["speaker_id"], [0.0, 0.0, 0.0])
        s[0] += d
        s[1] += overlap(w["start"], w["end"], me_iv)
        s[2] += overlap(w["start"], w["end"], oth_iv)
    scores = {sp: {"talk_sec": round(t, 1), "on_me": round(m / t, 2), "on_others": round(o / t, 2)}
              for sp, (t, m, o) in stats.items()}
    me = {sp for sp, sc in scores.items() if sc["on_me"] >= 0.5 and sc["on_me"] > sc["on_others"] + 0.2}
    return me, scores


# ---------- words → segments ----------

def build_segments(words, me_ids):
    names, segs, cur = {}, [], None

    def name(sp):
        if sp in me_ids:
            return "Me"
        if sp not in names:
            names[sp] = f"Speaker {len(names) + 1}"
        return names[sp]

    for w in words:
        if w["type"] == "audio_event":
            continue
        if w["type"] == "spacing":
            if cur:
                cur["text"] += w["text"]
            continue
        who = name(w.get("speaker_id") or "speaker_0")
        new = (cur is None or who != cur["speaker"] or w["start"] - cur["end"] > SEG_GAP
               or (cur["end"] - cur["start"] > SEG_MAX and cur["text"].rstrip()[-1:] in ".?!"))
        if new:
            if cur:
                segs.append(cur)
            cur = {"speaker": who, "start": w["start"], "end": w["end"], "text": w["text"]}
        else:
            cur["text"] += w["text"]
            cur["end"] = w["end"]
    if cur:
        segs.append(cur)
    for s in segs:
        s["text"] = " ".join(s["text"].split())
        s["start"], s["end"] = round(s["start"], 2), round(s["end"], 2)
    return [s for s in segs if s["text"]]


def to_plain(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "dict"):
        return json.loads(obj.json())
    return obj


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = Path(sys.argv[1]).resolve()
    audio = folder / "meeting.opus"
    if not audio.exists():
        sys.exit(f"No meeting.opus in {folder}")
    if not os.environ.get("ELEVENLABS_API_KEY"):
        sys.exit(f"ELEVENLABS_API_KEY not set (expected in {HOME / '.env.local'}). "
                 "Run: npx -y @elevenlabs/cli@latest onboard init  in the meetrec folder.")

    from elevenlabs import ElevenLabs

    keyterms = load_vocab()[:MAX_KEYTERMS]
    t0 = time.time()
    log(f"Uploading {audio.name} ({audio.stat().st_size / 1e6:.1f} MB) to ElevenLabs {MODEL}"
        f" · diarize · {len(keyterms)} keyterms…")
    client = ElevenLabs()
    with open(audio, "rb") as f:
        result = client.speech_to_text.convert(
            file=f,
            model_id=MODEL,
            language_code=LANG,
            secondary_language_codes=SECONDARY or None,
            diarize=True,
            timestamps_granularity="word",
            tag_audio_events=False,
            numbers_format="digits",
            keyterms=keyterms or None,
            request_options={"timeout_in_seconds": 3600},
        )
    raw = to_plain(result)
    write_atomic(folder / "transcript_11.raw.json", json.dumps(raw, indent=2, ensure_ascii=False))
    words = raw.get("words") or []
    duration = raw.get("audio_duration_secs") or (words[-1]["end"] if words else 0.0)
    log(f"  received {sum(w['type'] == 'word' for w in words)} words in {time.time() - t0:.1f}s")

    me_ids, scores = find_me(words, folder)
    for sp, sc in sorted(scores.items()):
        log(f"  {sp}: talks {sc['talk_sec']}s, {sc['on_me']:.0%} on mic track, "
            f"{sc['on_others']:.0%} on others track{'  → Me' if sp in me_ids else ''}")

    segs = build_segments(words, me_ids)
    elapsed = time.time() - t0
    md = write_outputs(folder, "11", segs, duration, f"ElevenLabs {MODEL}",
                       raw.get("language_code") or LANG or "?", elapsed,
                       {"model": MODEL, "keyterms": len(keyterms), "speakers": scores,
                        "me_speaker_ids": sorted(me_ids),
                        "transcription_id": raw.get("transcription_id")})
    log(f"Done: {len(segs)} segments, {duration:.0f}s of audio in {elapsed:.0f}s")
    log(f"  {md}")


if __name__ == "__main__":
    main()
