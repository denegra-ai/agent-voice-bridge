# Speech-to-text on WhatsApp voice notes: Turkish, Arabic, English

Which speech-to-text service should a WhatsApp agent use? We sent 120 voice notes in Turkish,
Arabic and English through the bridge's own `transcribe()` code, with six models from three
providers (Deepgram in two settings), and measured word error rate (WER), speed and cost. Everything here is reproducible
with the scripts in this folder.

**Measured November 2026.** Models and prices change; re-run before you rely on these numbers.

## Results

Language auto-detection, which is how the bridge runs by default (`STT_LANGUAGE` empty). Lower
WER is better. Each cell is the mean of two runs; the 95% interval comes from resampling clips.

| Model | Turkish WER | Arabic WER | English WER | p50 latency | List price / audio min |
|---|---|---|---|---|---|
| OpenAI gpt-4o-transcribe (Azure) | **3.1%** (1.9–4.3) | **3.7%** (1.9–6.1) | 3.2% (2.0–4.6) | 0.8 s | ≈ $0.0060 ¹ |
| ElevenLabs Scribe v2 | 3.2% (1.9–4.6) | 3.8% (1.7–6.4) | 3.5% (2.1–5.1) | 0.7 s | $0.0033 |
| OpenAI gpt-transcribe (Azure) | 4.0% (2.5–5.5) | 4.1% (2.2–6.5) | **2.9%** (1.9–4.2) | 0.8 s | $0.0045 |
| OpenAI gpt-4o-mini-transcribe (Azure) | 5.1% (3.1–7.6) | 6.6% (4.3–9.1) | 3.5% (2.2–4.8) | 0.7 s | ≈ $0.0030 ¹ |
| OpenAI Whisper (Azure) | 6.8% (4.7–9.1) | 7.7% (5.4–10.1) | 4.1% (2.6–5.7) | 1.6 s | $0.0066 ² |
| Deepgram Nova-3, `language=multi` | 8.8% (5.5–13.1) | 9.2% (6.4–12.3) | 7.5% (5.4–9.9) | 0.5 s | $0.0052 ³ |
| Deepgram Nova-3, `detect_language=true` ⁴ | 8.7% (5.1–13.0) | **104.6%** ⁴ | 10.3% (5.3–17.5) | 0.4 s | $0.0052 ³ |

Full table with every run, character error rate (CER), p95 latency, the language-hint variants
and the number check below: [`results/summary.md`](results/summary.md).

¹ Billed per audio token; per-minute figure is OpenAI's own estimate.
² Azure "Standard" (regional) Whisper; Global is $0.0060.
³ Nova-3 multilingual rate; with the language set (monolingual) it is $0.0043.
⁴ How the bridge called Deepgram up to v0.1.3. Deepgram detected most Arabic clips as English
and returned an **empty** transcript for 29 of 40 (in both runs). See below.

### Check: clips without numbers

FLEURS references write numbers sometimes as digits ("25") and sometimes as words ("on yedi"),
and each model picks its own format, so a correct number can count as an error. Leaving out
every clip where the reference or any model's transcript contains a digit (Turkish 8, Arabic
13, English 7 of 40) gives:

| Model | Turkish | Arabic | English |
|---|---|---|---|
| gpt-4o-transcribe | 2.7% | 2.3% | 2.4% |
| Scribe v2 | 2.4% | 1.8% | 2.4% |
| gpt-transcribe | 3.0% | 2.9% | 2.2% |
| gpt-4o-mini-transcribe | 3.1% | 4.7% | 2.9% |
| Whisper | 6.0% | 5.7% | 3.4% |
| Deepgram, `language=multi` | 5.5% | 5.2% | 6.2% |

The top three stay on top and Whisper stays about twice gpt-4o-transcribe in Turkish and
Arabic. Deepgram gains the most (it writes digits, with `smart_format=true`), so number
formatting explains part of its gap.

## What we learned

1. **Three models are tied at the top.** gpt-4o-transcribe, ElevenLabs Scribe v2 and
   gpt-transcribe all land at roughly 3–4% WER in all three languages, and their 95% intervals
   overlap. With 40 clips per language we can't honestly rank them against each other. Scribe v2
   has the lowest list price of the three.
2. **Newest is not best.** gpt-transcribe (model version 2026-07-28) did not beat
   gpt-4o-transcribe on Turkish or Arabic.
3. **One setting can silently drop a whole language.** The same Deepgram model, two ways of
   asking it to find the language: `detect_language=true` returned empty text for 29 of 40
   Arabic voice notes (and 2 of 40 English), `language=multi` for none. On an agent an empty
   transcript means the user gets no answer and no error. The bridge used `detect_language=true`
   up to v0.1.3; it now uses `language=multi`, and since v0.1.3 it can also retry an empty
   transcript with `STT_FALLBACK_PROVIDER`.
4. **A language hint barely matters for the top models.** For gpt-4o-transcribe, gpt-transcribe
   and Scribe v2 the hint changed WER by at most 0.4 points, sometimes for the worse. Deepgram
   gains in English (7.5% → 5.6%) and Arabic (9.2% → 8.1%), not in Turkish.
5. **Whisper makes about twice the errors of gpt-4o-transcribe in Turkish and Arabic, and is the
   slowest here.** It was the bridge's default until v0.1.3; this benchmark is why the default
   is now gpt-4o-transcribe, at about the same list price.
6. **Speed is not the bottleneck.** Every model except Whisper answers a voice note (median
   length 10 s) in under a second at the median.

## Method

- **Audio:** the first 40 clips per language, in archive order, from the test split of
  [Google FLEURS](https://huggingface.co/datasets/google/fleurs): Turkish `tr_tr`, Arabic
  `ar_eg` (speakers from Egypt reading Modern Standard Arabic sentences) and US English `en_us`.
  21.6 minutes in total, 4–24 s per clip (median 10 s). Each clip is re-encoded to mono
  Ogg/Opus at 16 kbit/s, the way WhatsApp sends voice notes.
- **Path:** every clip goes through `app.services.transcription.transcribe()`, the same function
  the bridge runs in production, one request at a time, with no fallback provider.
- **Runs:** two runs per model, each with auto-detection and again with the clip's language
  passed (`--hint`). The spread between runs is shown in the full table.
- **Scoring:** reference and transcript are normalised the same way for every model: Unicode
  NFKC, Arabic-Indic digits to ASCII, lowercase (Turkish `I`/`İ` handled, circumflex optional),
  punctuation and apostrophes removed, Arabic diacritics removed and alef forms, alef maqsura
  and ta marbuta unified. WER and CER are pooled per language (total edits / total reference
  words).
- **Failures:** a rate limit, timeout, connection error or 5xx after retries counts as *not
  measured*; other errors count as errors. Both are reported against the full clip count, and
  WER is computed over the measured clips. There were no errors. Four Arabic Whisper clips in
  one hinted run were not measured (connection errors on our side); every other run measured
  all 120 clips.
- **Providers:** Deepgram `nova-3` with `smart_format=true`; ElevenLabs `scribe_v2` with
  `tag_audio_events=false`; Azure OpenAI deployments of `whisper`, `gpt-4o-transcribe`
  (2025-03-20), `gpt-4o-mini-transcribe` (2025-12-15) and `gpt-transcribe` (2026-07-28) in
  East US 2.

## Caveats

- **Read speech, not real WhatsApp chat.** FLEURS speakers read sentences aloud in quiet rooms.
  Real voice notes are faster, noisier and full of dialect and code-switching, so expect higher
  WER everywhere. The ranking is the useful part, not the absolute numbers.
- **Small sample.** 40 clips per language; differences under about 1.5 points are noise.
- **Public test set.** FLEURS is public, so some models may have seen it in training. We can't
  rule that out for any model.
- **OpenAI models through Azure.** The OpenAI models ran on Azure OpenAI deployments, not on
  api.openai.com; results there may differ slightly.
- **Latency includes the network** from one client location to each provider's endpoint, so it
  says as much about distance as about the model.
- **One region, one month.** Azure East US 2, November 2026. Prices are list prices from each
  vendor's own pricing page or Azure's retail price API, read on 14 November 2026.

## Reproduce

```bash
.venv/bin/python bench/stt/prepare.py --per-language 40      # downloads ~100 MB, writes bench/stt/data/
.venv/bin/python -m bench.stt.run --provider deepgram --run 1
.venv/bin/python -m bench.stt.run --provider deepgram --run 1 --hint
.venv/bin/python -m bench.stt.run --provider elevenlabs --run 1
AZURE_OPENAI_STT_DEPLOYMENT=my-gpt-4o-transcribe \
  .venv/bin/python -m bench.stt.run --provider azure --name azure-gpt-4o-transcribe --run 1
.venv/bin/python -m bench.stt.score > bench/stt/results/summary.md
```

Keys are read from the environment or `.env`, the same way the bridge reads them. Everything in
the table, two runs each, with and without hints, costs about $2.70 at list prices. The
`deepgram-detect-language` results were recorded with bridge v0.1.3, which sent
`detect_language=true`; current code sends `language=multi`.

Audio and reference transcripts: Google FLEURS, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The transcripts in `results/` are
model output on that audio.
