#!/usr/bin/env python3
"""
Measure the local engine against ElevenLabs, and suggest vocabulary.

    compare.py <folder> [<folder> …] [--log]

For every recording that has both transcript_11.json (reference) and transcript_local.json
(hypothesis) it prints:
  * WER: word error rate of local vs ElevenLabs (lower is better). ElevenLabs isn't
    perfect, so this is "distance from ElevenLabs", but it tracks progress well.
  * the most common substitutions ("local heard X where ElevenLabs heard Y")
  * vocab candidates: names / jargon / ticket numbers ElevenLabs got that local missed,
    not yet in transcriber/vocab.txt

--log appends one line per recording to ../benchmarks.tsv so you can watch the local
engine improve over time (vocab growth, model changes).
"""
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import jiwer

from tx_common import HOME, load_vocab

FILLERS = {"um", "uh", "ah", "er", "eh", "hmm", "mm", "mhm", "uhm", "erm"}
DIGITS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
          "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def normalize(text):
    """Lowercase, drop punctuation/fillers/false starts, spoken digits → digits, skip non-Latin."""
    out, skipped = [], 0
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)      # ApiClient → Api Client
    for tok in text.split():
        if tok.endswith("-") and len(tok) <= 4:          # false start: "u-", "th-"
            continue
        if not re.search(r"[A-Za-z0-9]", tok):
            if re.search(r"\w", tok):                     # non-Latin script (e.g. Bengali)
                skipped += 1
            continue
        w = re.sub(r"[^a-z0-9]", "", tok.lower())
        if not w or w in FILLERS:
            continue
        w = DIGITS.get(w, w)
        if w.isdigit() and len(w) == 1 and out and out[-1].isdigit():   # "5 8 5" → "585"
            out[-1] += w
        else:
            out.append(w)
    return out, skipped


def vocab_keys():
    """Normalized vocab terms: 'ApiClient' → 'apiclient', 'Alex's team' → 'alexsteam'."""
    return {re.sub(r"[^a-z0-9]", "", v.lower()) for v in load_vocab()} - {""}


def vocab_words():
    """Individual normalized words that appear in any vocab term."""
    return {re.sub(r"[^a-z0-9]", "", w.lower())
            for v in load_vocab() for w in re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", v).split()} - {""}


def load(folder, suffix):
    f = folder / f"transcript_{suffix}.json"
    if not f.exists():
        return None, None
    data = json.loads(f.read_text(encoding="utf-8"))
    segs = sorted(data["segments"], key=lambda s: s["start"])
    return " ".join(s["text"] for s in segs), data.get("meta", {})


def interesting_terms(ref_text):
    """Words in the ElevenLabs text that look like names/jargon: CamelCase, ACRONYMS,
    numbers with 4+ digits, Capitalized words not at a sentence start."""
    terms = Counter()
    toks = ref_text.split()
    for i, raw in enumerate(toks):
        w = raw.strip(".,!?;:()\"'")
        if not w or not re.search(r"[A-Za-z0-9]", w):
            continue
        sentence_start = i == 0 or toks[i - 1][-1:] in ".?!"
        if (re.search(r"[a-z][A-Z]", w) or re.fullmatch(r"[A-Z]{2,}s?", w)
                or re.search(r"\d{4,}", w)
                or (w[0].isupper() and not sentence_start and w.lower() not in {"i", "i'm", "i've", "i'll"})):
            terms[w] += 1
    return terms


def compare(folder, log_file=None):
    ref_text, ref_meta = load(folder, "11")
    hyp_text, hyp_meta = load(folder, "local")
    if ref_text is None or hyp_text is None:
        print(f"skip {folder.name}: needs both transcript_11.json and transcript_local.json")
        return None
    keys, vwords = vocab_keys(), vocab_words()
    ref, skipped_ref = normalize(ref_text)
    hyp, _ = normalize(hyp_text)
    if not ref:
        print(f"skip {folder.name}: empty reference")
        return None

    out = jiwer.process_words(" ".join(ref), " ".join(hyp))
    subs, dels, ins = Counter(), Counter(), []
    ref_wrong = 0      # substitutions where local matches vocab.txt and ElevenLabs doesn't
    for chunk in out.alignments[0]:
        r = out.references[0][chunk.ref_start_idx:chunk.ref_end_idx]
        h = out.hypotheses[0][chunk.hyp_start_idx:chunk.hyp_end_idx]
        if chunk.type == "substitute":
            for a, b in zip(h, r):
                subs[(a, b)] += 1
                if (a in keys or a in vwords) and not (b in keys or b in vwords):
                    ref_wrong += 1
        elif chunk.type == "delete":
            for b in r:
                dels[b] += 1
        elif chunk.type == "insert":
            ins.append(" ".join(h))
    adj_wer = (out.substitutions - ref_wrong + out.deletions + out.insertions) / len(ref)

    vocab_lc = {v.lower().replace("-", "") for v in load_vocab()}
    hyp_set = set(hyp)
    candidates = []
    for term, n in interesting_terms(ref_text).most_common():
        key = re.sub(r"[^a-z0-9]", "", term.lower())
        if key and key not in hyp_set and key not in vocab_lc:
            candidates.append((term, n))

    model = hyp_meta.get("model", "?")
    vocab_n = hyp_meta.get("vocab_terms", 0)
    print(f"━━ {folder.name}  ({len(ref)} words; local = {model}, vocab {vocab_n} terms)")
    print(f"   WER {out.wer:6.1%}   (adjusted {adj_wer:.1%}: not counting {ref_wrong} spot(s) where local "
          f"matches vocab.txt and ElevenLabs doesn't)")
    print(f"   substitutions {out.substitutions} · deletions {out.deletions} · insertions {out.insertions}"
          + (f"   ({skipped_ref} non-Latin words skipped)" if skipped_ref else ""))
    if subs:
        print("   local heard → ElevenLabs heard:")
        for (a, b), n in subs.most_common(12):
            flag = "   ✓ local matches vocab" if (a in keys or a in vwords) and not (b in keys or b in vwords) else ""
            print(f"     {a!r:>18} → {b!r}" + (f"  ×{n}" if n > 1 else "") + flag)
    if dels:
        print("   local dropped: " + ", ".join(f"{w}" + (f"×{n}" if n > 1 else "") for w, n in dels.most_common(15)))
    if ins:
        print("   local added: " + " | ".join(ins[:8]))
    if candidates:
        print("   vocab candidates (add the real ones to transcriber/vocab.txt):")
        print("     " + ", ".join(f"{t}" + (f" ×{n}" if n > 1 else "") for t, n in candidates[:25]))

    if log_file:
        new = not log_file.exists()
        with log_file.open("a", encoding="utf-8") as f:
            if new:
                f.write("date\trecording\tref_words\twer\tadj_wer\tsub\tdel\tins\tlocal_model\tvocab_terms\n")
            f.write(f"{time.strftime('%Y-%m-%d %H:%M')}\t{folder.name}\t{len(ref)}\t{out.wer:.4f}\t{adj_wer:.4f}\t"
                    f"{out.substitutions}\t{out.deletions}\t{out.insertions}\t{model}\t{vocab_n}\n")
    return len(ref), out.wer


def main():
    args = [a for a in sys.argv[1:] if a != "--log"]
    log_file = HOME / "benchmarks.tsv" if "--log" in sys.argv else None
    if not args:
        sys.exit(__doc__)
    results = [r for r in (compare(Path(a).resolve(), log_file) for a in args) if r]
    if len(results) > 1:
        words = sum(n for n, _ in results)
        print(f"━━ overall: WER {sum(n * w for n, w in results) / words:.1%} over {words} words, "
              f"{len(results)} recordings")
    if log_file and results:
        print(f"(logged to {log_file})")


if __name__ == "__main__":
    main()
