"""Score every results/run*/<provider>.jsonl against the references and print a Markdown table.

    .venv/bin/python bench/stt/score.py > bench/stt/results/summary.md

WER and CER are pooled per provider and language (total edits / total reference words).
Clips that were not measured (rate limits, timeouts, provider errors) stay in the count
and are reported, never silently dropped. The spread across runs is the noise band.
"""

import json
import random
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
ARABIC_MARKS = re.compile("[ؐ-ًؚ-ٰٟـ]")
# Spelling variants that are not recognition errors: alef forms, alef maqsura, ta marbuta.
ARABIC_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه"})
# Arabic-Indic and Persian digits written by some models vs ASCII digits in the references.
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
# Turkish circumflex is optional in modern spelling (hâlâ / hala).
TURKISH_CIRCUMFLEX = str.maketrans("âîû", "aiu")


def normalize(text: str, lang: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(DIGITS)
    if lang == "tr":
        # Python's lower() maps I to i; Turkish needs I -> ı and İ -> i.
        text = text.replace("I", "ı").replace("İ", "i")
    text = text.lower()
    if lang == "tr":
        text = text.translate(TURKISH_CIRCUMFLEX)
    if lang == "ar":
        text = ARABIC_MARKS.sub("", text).translate(ARABIC_LETTERS)
    text = text.replace("'", "").replace("’", "")
    text = "".join(" " if unicodedata.category(ch)[0] in "PS" else ch for ch in text)
    return " ".join(text.split())


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    previous = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        current = [i]
        for j, h in enumerate(hyp, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1]


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def score_file(path: Path, references: dict[str, dict]) -> dict:
    rows = [json.loads(line) for line in path.open(encoding="utf-8")]
    if not rows:
        raise SystemExit(f"{path}: no rows")
    by_lang: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        clip = references.get(row["id"])
        if clip is None:
            raise SystemExit(
                f"{path}: clip {row['id']} is not in data/manifest.jsonl; re-run prepare.py"
            )
        bucket = by_lang[clip["lang"]]
        bucket["status"].append(row["status"])
        if row["status"] != "ok":
            continue
        ref = normalize(clip["reference"], clip["lang"])
        hyp = normalize(row["text"], clip["lang"])
        bucket["word_edits"].append(edit_distance(ref.split(), hyp.split()))
        bucket["words"].append(len(ref.split()))
        bucket["per_clip"].append((row["id"], bucket["word_edits"][-1], bucket["words"][-1]))
        ref_chars, hyp_chars = list(ref.replace(" ", "")), list(hyp.replace(" ", ""))
        bucket["char_edits"].append(edit_distance(ref_chars, hyp_chars))
        bucket["chars"].append(len(ref_chars))
        bucket["latency_ms"].append(row["latency_ms"])
    result = {}
    for lang, b in by_lang.items():
        result[lang] = {
            "clips": len(b["status"]),
            "ok": b["status"].count("ok"),
            "not_measured": b["status"].count("not_measured"),
            "error": b["status"].count("error"),
            "wer": sum(b["word_edits"]) / sum(b["words"]) if b["words"] else None,
            "cer": sum(b["char_edits"]) / sum(b["chars"]) if b["chars"] else None,
            "p50_ms": percentile(b["latency_ms"], 0.5) if b["latency_ms"] else None,
            "p95_ms": percentile(b["latency_ms"], 0.95) if b["latency_ms"] else None,
            "per_clip": b["per_clip"],
        }
    return result


def bootstrap_wer(runs: list[dict], samples: int = 2000) -> tuple[float, float]:
    """95% interval of pooled WER, resampling clips (each clip's edits averaged over runs)."""
    edits: dict[str, list[int]] = defaultdict(list)
    words: dict[str, int] = {}
    for run in runs:
        for clip_id, clip_edits, clip_words in run["per_clip"]:
            edits[clip_id].append(clip_edits)
            words[clip_id] = clip_words
    clips = [(statistics.mean(edits[c]), words[c]) for c in sorted(edits)]
    rng = random.Random(0)
    wers = sorted(
        sum(e for e, _ in pick) / sum(w for _, w in pick)
        for pick in (rng.choices(clips, k=len(clips)) for _ in range(samples))
    )
    return wers[int(0.025 * samples)], wers[int(0.975 * samples) - 1]


def number_clips(paths: list[Path], references: dict[str, dict]) -> set[str]:
    """Clips where the reference or any transcript has a digit.

    FLEURS references write numbers sometimes as digits and sometimes as words, and models
    choose their own format, so these clips can count a formatting choice as an error.
    """
    found = {cid for cid, clip in references.items() if re.search(r"\d", clip["reference"])}
    for path in paths:
        for row in map(json.loads, path.open(encoding="utf-8")):
            if row["status"] == "ok" and re.search(r"\d", row["text"].translate(DIGITS)):
                found.add(row["id"])
    return found


def pooled_wer(runs: list[dict], skip: set[str]) -> float | None:
    per_run = []
    for run in runs:
        kept = [(e, w) for cid, e, w in run["per_clip"] if cid not in skip]
        if kept:
            per_run.append(sum(e for e, _ in kept) / sum(w for _, w in kept))
    return statistics.mean(per_run) if per_run else None


def pct(value: float | None) -> str:
    return "–" if value is None else f"{100 * value:.1f}%"


def main() -> None:
    references = {
        clip["id"]: clip
        for clip in map(json.loads, (HERE / "data" / "manifest.jsonl").open(encoding="utf-8"))
    }
    scores: dict[tuple[str, str], list[dict]] = defaultdict(list)
    paths = sorted((HERE / "results").glob("run[1-9]*/*.jsonl"))
    skip = number_clips(paths, references)
    for path in paths:
        for lang, s in score_file(path, references).items():
            scores[(path.stem, lang)].append(s)
    if not scores:
        raise SystemExit("no results to score")

    print(
        "| Provider | Lang | Runs | WER (mean, range) | 95% CI | CER | p50 / p95 latency "
        "| Not measured | Errors | WER without number clips |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|")
    for (provider, lang), runs in sorted(scores.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        wers = [r["wer"] for r in runs if r["wer"] is not None]
        cers = [r["cer"] for r in runs if r["cer"] is not None]
        wer = pct(statistics.mean(wers)) if wers else "–"
        spread = f" ({pct(min(wers))}–{pct(max(wers))})" if len(wers) > 1 else ""
        p50s = [r["p50_ms"] for r in runs if r["p50_ms"] is not None]
        p95s = [r["p95_ms"] for r in runs if r["p95_ms"] is not None]
        latency = (
            f"{statistics.median(p50s) / 1000:.1f}s / {statistics.median(p95s) / 1000:.1f}s"
            if p50s
            else "–"
        )
        not_measured = sum(r["not_measured"] for r in runs)
        errors = sum(r["error"] for r in runs)
        total = sum(r["clips"] for r in runs)
        low, high = bootstrap_wer(runs) if wers else (None, None)
        print(
            f"| {provider} | {lang} | {len(runs)} | {wer}{spread} | {pct(low)}–{pct(high)} | "
            f"{pct(statistics.mean(cers)) if cers else '–'} | "
            f"{latency} | {not_measured} of {total} | {errors} of {total} | "
            f"{pct(pooled_wer(runs, skip))} |"
        )
    counts = {
        lang: sum(1 for c in skip if references[c]["lang"] == lang) for lang in ("tr", "ar", "en")
    }
    print(
        "\nNumber clips (a digit in the reference or in any transcript), left out of the last "
        "column: " + ", ".join(f"{lang} {n} of 40" for lang, n in counts.items()) + "."
    )


if __name__ == "__main__":
    main()
