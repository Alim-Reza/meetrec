# meetrec: record and transcribe any meeting on Linux

Records **everything you hear** (Teams, a Chrome tab, Zoom, Meet, Slack huddles, anything)
plus **your microphone**, through any output (laptop speakers, USB or Bluetooth headset, HDMI).
When you stop, it **transcribes the meeting locally with Whisper**, with "Me" / "Them" labels.
Nothing leaves your laptop. No browser extension, and no bot joins the call.

```
Super+Shift+R             # 🔴 start … Super+Shift+R again ⏹ stop → transcript appears a bit later
meetrec start standup     # same, from a terminal (name is optional)
meetrec stop
meetrec status            # recording? transcription running or queued?
meetrec list              # recordings + transcript status
meetrec open [rec] [11|local]           # open a transcript (default: latest)
meetrec transcribe [rec] [--engine 11|local]   # (re)transcribe now (default: both engines)
meetrec transcribe --missing [--engine 11]     # fill in missing transcripts
meetrec recover           # finish recordings interrupted by a crash or reboot
meetrec doctor            # check that everything is installed and working
```

Each meeting gets its own folder:

```
~/Recordings/meetings/2026-11-23_14-00-05_standup/
├── meeting.opus             ← both sides mixed: listen to this one
├── others.opus              ← what came out of your speakers (everyone else)
├── me.opus                  ← your microphone only
├── transcript_11.md         ← ElevenLabs Scribe: Me / Speaker 1 / Speaker 2 …
├── transcript_11.srt / .json
├── transcript_11.raw.json   ← the untouched API response (word timings, speaker ids)
├── transcript_local.md      ← local Whisper: Me / Them
├── transcript_local.srt / .json
└── transcribe_11.log, transcribe_local.log
```

### Two engines, side by side

| | `transcript_11.*` (ElevenLabs) | `transcript_local.*` (Whisper) |
|---|---|---|
| Runs | in the cloud (uploads `meeting.opus`) | on this laptop, offline |
| Speakers | Me + Speaker 1, 2, … (diarization) | Me / Them (from the two tracks) |
| Vocabulary | `transcriber/vocab.txt` sent as keyterms | `transcriber/vocab.txt` sent as hotwords |
| Speed | seconds to a minute | large-v3: ~1–2× real-time (background, low priority) |
| Cost | free tier ≈ 4.5 h, then $0.20/h (+keyterms) | free |

### Improving the local engine (goal: replace ElevenLabs)

Every meeting is transcribed by both engines, then **automatically scored**: local vs
ElevenLabs, saved to `compare.txt` in the recording folder plus one line in `benchmarks.tsv`.
`meetrec compare` shows:

* **WER** (word error rate) of local against ElevenLabs, plus an **adjusted WER** that doesn't
  count spots where local matches `vocab.txt` and ElevenLabs doesn't (ElevenLabs makes
  mistakes too, e.g. it heard "Alexis" for *Alex's*).
* what local heard against what ElevenLabs heard, and words local dropped or added
* **vocab candidates**: names, jargon and ticket numbers ElevenLabs got that local missed

The loop: **after meetings → `meetrec compare --all` → add the real terms to `vocab.txt`
(`meetrec vocab`) → future meetings improve.** Re-run old ones with
`meetrec transcribe <rec> --engine local` to measure the effect.

What was tried on the 2-minute standup of 2026-11-24 (181 words):

| Local setup | Adjusted WER | Speed | Notes |
|---|---|---|---|
| large-v3-turbo, no vocab | 12.2%* | 3.7× | lowercase run-on, "didn't create" (meaning flipped), "roller", "allowed" for alert |
| large-v3-turbo + vocab | 19.9% | 3.7× | vocab terms right and punctuation back, but invents English over the Bengali sentence |
| turbo + vocab + per-window language detection | 19.9% | 2.9× | no change: the Bengali sentence shares a 30 s window with English |
| **large-v3 + vocab (default)** | **14.4%** | 1–2× | "tightened" (correct; ElevenLabs said "added"), all vocab right, punctuated; translates the Bengali sentence instead of inventing |

\* By the number, the original setup "wins". But on 181 words the score is noisy, and it hides
what matters. A meaning-flipping error ("didn't create") counts the same as a dropped "the".
large-v3 also loses about 10 points to things that aren't really errors: translating the Bengali
sentence (about 10 extra words), being right where ElevenLabs was wrong ("tightened" against
"added"), and skipping repeated false starts. Read side by side, large-v3 + vocab is clearly
the best transcript. The score becomes trustworthy once there are a few hours of meetings in
`benchmarks.tsv`.

Ideas not done yet:
* **Mixed-language chunks:** detect the language per short speech chunk and transcribe Bengali
  chunks as Bengali (currently large-v3 translates them to English).
* **Fine-tuning Whisper on your own meetings:** every `transcript_11.raw.json` (word timings)
  plus `meeting.opus` is a labelled training example. After a few hours of meetings, a
  LoRA fine-tune of Whisper (needs a GPU, e.g. one cloud session) could learn your team's
  voices and vocabulary beyond what a prompt can do.

Both run after every stop (ElevenLabs first). Change `MEETREC_ENGINES` in `config.env`
to run only one. **Add your team's names, code terms and ticket prefixes to
`transcriber/vocab.txt`.** That's what fixes errors like "user count service" → `UserAccountService`.
`vocab.txt` is git-ignored; `install.sh` creates it from `transcriber/vocab.example.txt`.

How the ElevenLabs engine identifies **you** without paying twice: only the mix is uploaded,
with diarization on. Then, locally, the speech regions on `me.opus` and `others.opus`
(Silero VAD) are compared against each diarized speaker's word timings. The speaker who
lines up with your mic track and not the other track is labelled "Me".

The API key is in `.env.local`. `npx @elevenlabs/cli onboard` wrote it there, it's git-ignored,
and the code reads it via `python-dotenv`. It never appears in the code or `config.env`.

Sample `transcript.md`:

```markdown
# Meeting 2026-11-23 14:00 — standup
- **Duration:** 00:42:10 · **Model:** large-v3-turbo · **Language:** en

**[00:03:12] Me:** Can we ship it Friday?

**[00:03:15] Them:** Only if QA signs off by Thursday.
```

---

## Project layout

Everything lives in this folder, including the Python environment and the Whisper model.
Outside it there's only a symlink, an app-menu entry, the optional shortcut, and your recordings.

```
meetrec/
├── meetrec                   CLI entry point: loads config + modules, dispatches commands
├── config.env                all settings (model, language, bitrates, auto-transcribe, …)
├── install.sh                installer (see below)
├── lib/common.sh             shared helpers (output, notifications, paths)
├── voice-catcher/catcher.sh  stage 1: record (PipeWire → raw PCM → Opus)
├── .env.local                ElevenLabs API key (written by the ElevenLabs CLI, git-ignored)
├── transcriber/              stage 2: speech-to-text
│   ├── transcriber.sh        queue + background worker, runs each engine in MEETREC_ENGINES
│   ├── transcribe_11.py      ElevenLabs Scribe v3 → transcript_11.*
│   ├── transcribe_local.py   faster-whisper → transcript_local.*
│   ├── tx_common.py          shared output writers (.md / .srt / .json)
│   ├── compare.py            local vs ElevenLabs scoring + vocab suggestions
│   ├── vocab.txt             your names & jargon → ElevenLabs keyterms
│   └── requirements.txt
├── summarizer/README.md      stage 3: planned (LLM summary, action items)
├── .venv/                    private Python env (created by install.sh, git-ignored)
└── models/                   Whisper model files, ~1.6 GB (git-ignored)
```

`~/.local/bin/meetrec` is a **symlink** to `./meetrec`, so edits here take effect immediately.

## Pipeline

```
 Teams/Chrome/Zoom ─► PipeWire ─► default output ─► monitor ─► pw-record ─► others.raw ┐
 your mic ──────────────────────────────────────────────────► pw-record ─► me.raw ────┤
                                                                                        │ meetrec stop
                                                       ffmpeg: mix + encode to Opus ◄───┘
                                                                     │
                                       background, nice 15, one at a time (flock)
                                                                     ▼
                         faster-whisper large-v3-turbo on me.opus ("Me") + others.opus ("Them")
                                       → remove echo → merge by time → transcript.*
                                                                     │
                                                       notification: 📝 Transcript ready
```

### Stage 1: voice-catcher
* Every PipeWire output has a **monitor** (a copy of everything played).
  `pw-record -P '{ stream.capture.sink = true }'` records it, so every participant in any app is captured.
* A second `pw-record` records the default mic.
* Neither stream sets `--target`, so WirePlumber keeps them on the *current default* device.
  Switching headsets mid-call is fine.
* Audio is written as **raw PCM** while recording. There's no header to finalize, so a crash or
  power loss loses nothing, and `meetrec recover` finishes the files later.
* On stop, ffmpeg mixes the tracks (no auto-normalize, a limiter against clipping) and
  encodes Opus. Raw files are deleted only when encoding succeeds.

### Stage 2: transcriber
* **faster-whisper** (CTranslate2, int8 on CPU) with **large-v3-turbo**: near-large-v3
  accuracy for English at a fraction of the cost. This laptop has no NVIDIA GPU; on the
  Core Ultra 7 it ran at about 1.8× real time for a short clip, model loading included.
  A 1-hour meeting should take roughly 20–40 minutes in the background.
* **Speaker labels without diarization:** `me.opus` and `others.opus` are transcribed separately
  and merged by timestamp. (Telling the *other* people apart would need pyannote; not done.)
* **Voice-activity detection** skips silence. That's faster, and it stops Whisper from
  inventing text during quiet parts.
* `condition_on_previous_text=False` avoids the repetition loops Whisper falls into on long audio.
* **Echo removal:** when you use speakers, the mic also picks up the other side. "Me" segments
  whose words mostly match a "Them" segment at the same moment are dropped.
* It runs under `nice 15` / `ionice idle`, so the laptop stays responsive. A lock file makes
  back-to-back meetings queue instead of competing for the CPU.

### Stage 3: summarizer (planned)
See [`summarizer/README.md`](summarizer/README.md).

---

## Install

```bash
cd ~/personal/meetrec
./install.sh --deps --shortcut
```

| Flag | Effect |
|---|---|
| *(none)* | symlink the CLI, create `.venv`, install faster-whisper, download the model, app-menu launcher |
| `--deps` | `sudo apt install ffmpeg pulseaudio-utils libnotify-bin pipewire-bin wireplumber python3-pip` |
| `--shortcut` | GNOME shortcut **Super+Shift+R** → `meetrec toggle` |
| `--no-model` | skip the model download (it then happens on the first transcription) |
| `--uninstall` | remove the symlink, launcher and shortcut (keeps this folder and your recordings) |

### Dependencies and why

| Tool | Package | Used by | Why |
|---|---|---|---|
| `pw-record` | `pipewire-bin` (preinstalled) | voice-catcher | capture the output monitor and mic from PipeWire |
| `wpctl` | `wireplumber` (preinstalled) | voice-catcher | device names |
| `ffmpeg` | `ffmpeg` | voice-catcher | mix tracks, encode Opus (`libopus`) |
| `pactl` | `pulseaudio-utils` | (debugging) | `pactl list short sources` and similar |
| `notify-send` | `libnotify-bin` (preinstalled) | all | desktop notifications (needed with the hotkey) |
| `faster-whisper` | pip → `.venv` | transcriber | Whisper inference on CPU |
| `av` < 16 | pip → `.venv` | transcriber | audio decoding; faster-whisper 1.2.1 breaks on PyAV 16+ |
| Whisper model | Hugging Face → `models/` | transcriber | `large-v3-turbo`, ~1.6 GB, downloaded once |

---

## How it was built

1. **Checked the audio stack:** PipeWire 1.0.5 + WirePlumber 0.4.17 on Ubuntu 24.04.
   `pw-record` was present; `ffmpeg` and `pactl` were installed with apt.
2. **Proved the capture:** 2 s from the output monitor and the mic to raw stdout. Byte
   counts matched 48 kHz × channels × 2 bytes × seconds.
3. **voice-catcher:** two background `pw-record` processes under `setsid` (so they survive the
   terminal closing), PIDs in `$XDG_RUNTIME_DIR`. `stop` sends SIGINT, then ffmpeg encodes.
4. **Bugs found while testing:**
   * `ffmpeg … | grep -q libopus` under `pipefail` sometimes failed on SIGPIPE, giving a false
     "no libopus" warning. The fix captures the output first.
5. **Transcriber:**
   * No `python3-venv` on the system (installing it needs sudo), so `.venv` is created with
     `--without-pip` and filled with `pip3 --python .venv/bin/python install …`.
   * The latest PyAV (19.x) removed an `av.open()` argument that faster-whisper 1.2.1 uses,
     so `av<16` is pinned in `requirements.txt`.
   * Tested on a real recording (Teams call, Jabra USB headset): 44 s of audio was
     transcribed in 24 s, with correct Me/Them separation.
   * Tested the full chain: `start` → `stop` → background worker → `transcript.*` written →
     markers cleaned up.
6. **Reorganized** into `voice-catcher/`, `transcriber/` and `summarizer/` with one `config.env`.

---

## Settings (`config.env`)

| Variable | Default | |
|---|---|---|
| `MEETREC_DIR` | `~/Recordings/meetings` | where recordings go |
| `MEETREC_AUTO_TRANSCRIBE` | `1` | transcribe automatically after every stop |
| `MEETREC_MODEL` | `large-v3-turbo` | `large-v3` (more accurate, slower), `distil-large-v3` (English, faster), `small.en`, … |
| `MEETREC_LANG` | `en` | `bn` for Bengali, `auto` to detect |
| `MEETREC_THREADS` | `0` (auto = half the cores) | CPU threads for Whisper |
| `MEETREC_BR_MIX` / `_OTHERS` / `_ME` | `64k` / `48k` / `32k` | Opus bitrates |

Each value can be overridden for one run, e.g. `MEETREC_LANG=bn MEETREC_MODEL=large-v3 meetrec transcribe`
(a new model downloads on first use).

## Things to know

* **Muted speakers still record.** The monitor is taken before the volume or mute stage.
* **The meeting app's mute button doesn't affect `me.opus`.** System-level (GNOME) mic mute does.
* **Bluetooth headsets** switch to low-quality "HFP" audio while their mic is in use, so
  recordings sound phone-like. USB headsets (like the Jabra) don't have this problem.
* **System sounds** are recorded too, since they go to the same output.
* **Consent:** many places require all parties' consent to record. Nothing here shows a
  banner the way Teams' own recording does, so tell people.
