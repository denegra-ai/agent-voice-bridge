# Speech-to-text on WhatsApp voice notes: Turkish, Arabic, English

Which speech-to-text service should a WhatsApp agent use? We sent 120 voice notes in Turkish,
Arabic and English through the bridge's own `transcribe()` code, with seven models from four
providers, and measured word error rate (WER), speed and cost. Everything here is reproducible
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
| OpenAI gpt-4o-mini-transcribe (Azure) | 5.1% | 6.6% | 3.5% | 0.7 s | ≈ $0.0030 ¹ |
| OpenAI Whisper (Azure) | 6.8% (4.7–9.1) | 7.8% (5.6–10.2) | 4.1% (2.6–5.7) | 1.6 s | $0.0066 ² |
| Deepgram Nova-3 | 8.7% (5.1–13.0) | **104.6%** ³ | 10.3% (5.3–17.5) | 0.4 s | $0.0052 ⁴ |

Full table with every run, character error rate (CER), p95 latency and the language-hint
variants: [`results/summary.md`](results/summary.md).

¹ Billed per audio token; per-minute figure is OpenAI's own estimate.
² Azure "Standard" (regional) Whisper; Global is $0.0060.
³ Deepgram's auto-detection heard most Arabic clips as English and returned an **empty**
transcript for 29 of 40. See below.
⁴ Nova-3 multilingual, which language detection uses; monolingual is $0.0043.

## What we learned

1. **Three models are tied at the top.** gpt-4o-transcribe, ElevenLabs Scribe v2 and
   gpt-transcribe all land at roughly 3–4% WER in all three languages, and their 95% intervals
   overlap. With 40 clips per language we can't honestly rank them against each other. Scribe v2
   is the cheapest of the three.
2. **Newest is not best.** gpt-transcribe (released July 2026) did not beat gpt-4o-transcribe on
   Turkish or Arabic.
3. **Auto-detection can silently drop a whole language.** Deepgram Nova-3 with
   `detect_language` returned empty text for 29 of 40 Arabic voice notes and transcribed one
   English clip as Swedish. On an agent that means the user gets no answer and no error. With the
   language set (`STT_LANGUAGE=ar`) Deepgram's Arabic WER drops to 14.5%. If you use Deepgram,
   set the language.
4. **A language hint does not help the top models.** For gpt-4o-transcribe, gpt-transcribe and
   Scribe v2 the hint changed WER by less than half a point, sometimes for the worse. Only
   Deepgram depends on it.
5. **Whisper is the oldest, slowest and most expensive here, and makes about twice the errors of
   the top three in Turkish and Arabic.** It is still the bridge's default (`STT_PROVIDER=openai`
   with `whisper-1`) because it is the most widely available; if you can, pick one of the top
   three.
6. **Speed is not the bottleneck.** Every model except Whisper answers a 10-second voice note in
   under a second at the median. Deepgram is the fastest (0.4 s).

## Method

- **Audio:** the first 40 clips per language, in archive order, from the test split of
  [Google FLEURS](https://huggingface.co/datasets/google/fleurs) (Turkish `tr_tr`, Egyptian
  Arabic `ar_eg`, US English `en_us`). 21.6 minutes in total, 4–24 s per clip. Each clip is
  re-encoded to mono Ogg/Opus at 16 kbit/s, the way WhatsApp sends voice notes.
- **Path:** every clip goes through `app.services.transcription.transcribe()`, the same function
  the bridge runs in production, one request at a time.
- **Runs:** two runs per model, each with auto-detection and again with the clip's language
  passed (`--hint`). The spread between runs is shown in the full table.
- **Scoring:** reference and transcript are normalised the same way: Unicode NFKC, lowercase
  (Turkish `I`/`İ` handled), punctuation and apostrophes removed, Arabic diacritics and alef
  variants unified. WER and CER are pooled per language (total edits / total reference words).
- **Failures:** a rate limit, timeout, connection error or 5xx after retries counts as *not
  measured*; other errors count as errors. Both are reported against the full clip count, and
  WER is computed over the measured clips. There were no errors. Four Arabic Whisper clips in
  one hinted run were not measured (connection errors on our side); every other run measured
  all 120 clips.
- **Providers:** Deepgram `nova-3`; ElevenLabs `scribe_v2` with `tag_audio_events=false`; Azure
  OpenAI deployments of `whisper`, `gpt-4o-transcribe` (2025-03-20), `gpt-4o-mini-transcribe`
  (2025-12-15) and `gpt-transcribe` (2026-07-28) in East US 2.

## Caveats

- **Read speech, not real WhatsApp chat.** FLEURS speakers read sentences aloud in quiet rooms.
  Real voice notes are faster, noisier and full of dialect and code-switching, so expect higher
  WER everywhere. The ranking is the useful part, not the absolute numbers.
- **Small sample.** 40 clips per language; differences under about 1.5 points are noise.
- **Numbers.** References spell numbers out ("on yedi") while some models write digits ("17").
  That counts as a word error and slightly penalises models that format numbers.
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

Keys are read from the environment or `.env`, the same way the bridge reads them. Running all
seven models twice, with and without hints (about 86 audio minutes each), costs about $2.50 at
list prices.

Audio and reference transcripts: Google FLEURS, licensed
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The transcripts in `results/` are
model output on that audio.
