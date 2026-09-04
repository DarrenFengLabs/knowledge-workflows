#!/usr/bin/env python3
"""Merge per-chunk SRT files and offset timestamps by WAV durations."""

from __future__ import annotations

import argparse
import re
import wave
from pathlib import Path


TIMESTAMP = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})(?P<suffix>.*)"
)


def parse_timestamp(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, milliseconds = rest.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )


def format_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as wav_file:
        return round(wav_file.getnframes() * 1_000 / wav_file.getframerate())


def parse_cues(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    cues: list[list[str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text):
        lines = block.splitlines()
        timestamp_index = next(
            (index for index, line in enumerate(lines) if TIMESTAMP.fullmatch(line)),
            None,
        )
        if timestamp_index is not None:
            cues.append(lines[timestamp_index:])
    return cues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--chunks-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    wav_files = sorted(args.chunks_dir.glob("chunk_*.wav"))
    if not wav_files:
        parser.error(f"no chunk_*.wav files found in {args.chunks_dir}")

    output_blocks: list[str] = []
    offset_ms = 0
    cue_number = 1

    for wav_path in wav_files:
        srt_path = wav_path.with_suffix(".srt")
        if not srt_path.is_file():
            parser.error(f"missing SRT for {wav_path.name}: {srt_path}")

        for cue in parse_cues(srt_path):
            match = TIMESTAMP.fullmatch(cue[0])
            if match is None:
                continue
            start = format_timestamp(parse_timestamp(match["start"]) + offset_ms)
            end = format_timestamp(parse_timestamp(match["end"]) + offset_ms)
            timing = f"{start} --> {end}{match['suffix']}"
            output_blocks.append("\n".join([str(cue_number), timing, *cue[1:]]))
            cue_number += 1

        offset_ms += wav_duration_ms(wav_path)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    content = "\n\n".join(output_blocks)
    args.output.write_text(content + ("\n" if content else ""), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
