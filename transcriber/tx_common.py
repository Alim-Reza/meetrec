"""Shared helpers for the transcription engines (transcribe_local.py, transcribe_11.py).

Every engine produces a list of segments  {"speaker", "start", "end", "text"}  and calls
write_outputs(), which writes transcript_<suffix>.md / .srt / .json into the recording folder.
"""
import json
import re
import time
from pathlib import Path

HOME = Path(__file__).resolve().parent.parent          # the meetrec folder
PARAGRAPH_GAP = 3.0       # merge same-speaker segments closer than this (s) in the .md …
PARAGRAPH_MAX = 45.0      # … but start a new paragraph after this long, at a sentence end


def log(msg=""):
    print(msg, flush=True)


def ts(sec, srt=False):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}" if srt else f"{h:02d}:{m:02d}:{s:02d}"


def load_vocab():
    """transcriber/vocab.txt: one term per line, '#' comments. Used to bias recognition."""
    f = HOME / "transcriber" / "vocab.txt"
    if not f.exists():
        return []
    terms = []
    for line in f.read_text(encoding="utf-8").splitlines():
        t = line.split("#", 1)[0].strip()
        if t and t not in terms:
            terms.append(t)
    return terms


def title_from_folder(folder):
    m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-\d{2}(?:_(.*))?$", folder.name)
    if not m:
        return folder.name
    date, hh, mm, name = m.groups()
    return f"{date} {hh}:{mm}" + (f" — {name.replace('-', ' ')}" if name else "")


def paragraphs(segs):
    paras = []
    for s in segs:
        p = paras[-1] if paras else None
        if (p and p["speaker"] == s["speaker"] and s["start"] - p["end"] < PARAGRAPH_GAP
                and not (p["end"] - p["start"] > PARAGRAPH_MAX and p["text"].rstrip()[-1:] in ".?!")):
            p["text"] += " " + s["text"]
            p["end"] = s["end"]
        else:
            paras.append(dict(s))
    return paras


def write_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def write_outputs(folder, suffix, segs, duration, engine_desc, language, elapsed, extra_meta=None):
    """Write transcript_<suffix>.{json,srt,md}. Returns the .md path."""
    segs = sorted(segs, key=lambda s: (s["start"], s["speaker"]))
    meta = {
        "title": title_from_folder(folder),
        "engine": engine_desc,
        "duration_sec": round(duration, 1),
        "language": language,
        "transcribe_sec": round(elapsed, 1),
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **(extra_meta or {}),
    }
    base = folder / f"transcript_{suffix}"

    write_atomic(base.with_suffix(".json"),
                 json.dumps({"meta": meta, "segments": segs}, indent=2, ensure_ascii=False))

    srt = [f"{i}\n{ts(s['start'], True)} --> {ts(s['end'], True)}\n{s['speaker']}: {s['text']}\n"
           for i, s in enumerate(segs, 1)]
    write_atomic(base.with_suffix(".srt"), "\n".join(srt))

    speed = f" ({duration / elapsed:.1f}× real-time)" if elapsed > 0 else ""
    md = [f"# Meeting {meta['title']}", "",
          f"- **Duration:** {ts(duration)}  ",
          f"- **Engine:** {engine_desc} · **Language:** {language}  ",
          f"- **Transcribed in:** {ts(elapsed)}{speed}", ""]
    if not segs:
        md.append("_No speech detected._")
    for p in paragraphs(segs):
        md += [f"**[{ts(p['start'])}] {p['speaker']}:** {p['text']}", ""]
    write_atomic(base.with_suffix(".md"), "\n".join(md))
    return base.with_suffix(".md")
