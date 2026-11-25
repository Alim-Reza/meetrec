# summarizer (planned)

The next pipeline stage after `transcriber/`.

**Input:** `transcript.json` in a recording folder (segments with speaker, start, end, text).
**Output:** `summary.md`: a TL;DR, decisions, action items (who / what / when), open questions.

Things still to decide:
- Which LLM: the company AI gateway, the Claude API, or a local model (e.g. via Ollama).
- Privacy: transcription is fully local, but a hosted LLM means the meeting text leaves this
  laptop, so make it opt-in (`MEETREC_AUTO_SUMMARIZE=0` by default).
- Long meetings: chunk the transcript, summarize each chunk, then summarize the summaries.

Hook point: `transcriber_worker` in `../transcriber/transcriber.sh`, after a successful transcription.
