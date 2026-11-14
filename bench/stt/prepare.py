"""Build the benchmark set: FLEURS test clips re-encoded the way WhatsApp sends voice notes.

    .venv/bin/python bench/stt/prepare.py --per-language 40

Streams each language's FLEURS test archive only until the first N clips in archive order
are read, so nothing is hand-picked. Writes Ogg/Opus mono files and
bench/stt/data/manifest.jsonl. FLEURS: https://huggingface.co/datasets/google/fleurs (CC BY 4.0).
"""

import argparse
import csv
import io
import json
import subprocess
import tarfile
from pathlib import Path

import httpx

FLEURS = "https://huggingface.co/datasets/google/fleurs/resolve/main/data"
LANGUAGES = {"tr_tr": "tr", "ar_eg": "ar", "en_us": "en"}
DATA_DIR = Path(__file__).parent / "data"


def read_references(fleurs_lang: str) -> dict[str, dict]:
    tsv = httpx.get(f"{FLEURS}/{fleurs_lang}/test.tsv", follow_redirects=True, timeout=60)
    tsv.raise_for_status()
    reader = csv.reader(io.StringIO(tsv.text), delimiter="\t", quoting=csv.QUOTE_NONE)
    # columns: id, file name, raw transcription, normalized transcription, ...
    return {row[1]: {"fleurs_id": row[0], "reference": row[3]} for row in reader}


def to_whatsapp_opus(wav: bytes, target: Path) -> float:
    # WhatsApp voice notes are mono Opus around 16 kbit/s.
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libopus",
            "-b:a",
            "16k",
            str(target),
        ],
        input=wav,
        check=True,
    )
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "csv=p=0",
            str(target),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(probe.stdout.strip())


def prepare_language(fleurs_lang: str, lang: str, per_language: int) -> list[dict]:
    references = read_references(fleurs_lang)
    clips = []
    url = f"{FLEURS}/{fleurs_lang}/audio/test.tar.gz"
    with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
        response.raise_for_status()
        stream = io.BufferedReader(_ResponseReader(response.iter_bytes()))
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
            for member in archive:
                name = Path(member.name).name
                if name not in references:
                    continue
                reference = references[name]
                wav = archive.extractfile(member).read()
                target = DATA_DIR / lang / f"{Path(name).stem}.ogg"
                target.parent.mkdir(parents=True, exist_ok=True)
                seconds = to_whatsapp_opus(wav, target)
                clips.append(
                    {
                        "id": f"{lang}-{reference['fleurs_id']}-{Path(name).stem}",
                        "lang": lang,
                        "file": str(target.relative_to(DATA_DIR)),
                        "seconds": round(seconds, 2),
                        "reference": reference["reference"],
                    }
                )
                if len(clips) == per_language:
                    break
    if len(clips) != per_language:
        raise SystemExit(f"{fleurs_lang}: found {len(clips)} of {per_language} clips")
    return clips


class _ResponseReader(io.RawIOBase):
    def __init__(self, chunks):
        self._chunks = chunks
        self._buffer = b""

    def readable(self) -> bool:
        return True

    def readinto(self, target) -> int:
        while not self._buffer:
            try:
                self._buffer = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(target), len(self._buffer))
        target[:size] = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-language", type=int, default=40)
    args = parser.parse_args()
    clips = []
    for fleurs_lang, lang in LANGUAGES.items():
        clips += prepare_language(fleurs_lang, lang, args.per_language)
        print(f"{lang}: {sum(c['lang'] == lang for c in clips)} clips")
    with (DATA_DIR / "manifest.jsonl").open("w", encoding="utf-8") as out:
        for clip in clips:
            out.write(json.dumps(clip, ensure_ascii=False) + "\n")
    minutes = sum(c["seconds"] for c in clips) / 60
    print(f"{len(clips)} clips, {minutes:.1f} minutes of audio")


if __name__ == "__main__":
    main()
