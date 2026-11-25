"""Shared data model for Spreadtracker.

This module contains the pieces that both frontends need: the classic MOD
parser, the canonical cell/period tables, and the frontend-independent ``Song``
container consumed by the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_NAME = "Spreadtracker"
DEFAULT_WAV = "spreadtracker.wav"

PATTERN_ROWS = 64
CHANNEL_COUNT = 4
AMIGA_CLOCK = 7093789.2

# Worksheet layout constants. The Excel VBA and Calc macros mirror these.
PATTERN_FIRST_HEADER_ROW = 4
PATTERN_BLOCK_ROWS = 66  # 1 header + 1 column header + 64 data rows
PATTERN_FIRST_DATA_ROW = PATTERN_FIRST_HEADER_ROW + 2  # 6 (1-based)
ORDER_FIRST_ROW = 23  # 1-based
INSTRUMENT_FIRST_ROW = 4  # 1-based
BASE64_CHUNK_CHARS = 96

# Protracker finetune-0 period table: C-1 .. B-3.
PERIODS: tuple[int, ...] = (
    856, 808, 762, 720, 678, 640, 604, 570, 538, 508, 480, 453,
    428, 404, 381, 360, 339, 320, 302, 285, 269, 254, 240, 226,
    214, 202, 190, 180, 170, 160, 151, 143, 135, 127, 120, 113,
)
NOTE_NAMES: tuple[str, ...] = tuple(
    f"{name}{octave}"
    for octave in (1, 2, 3)
    for name in ("C-", "C#", "D-", "D#", "E-", "F-", "F#", "G-", "G#", "A-", "A#", "B-")
)
PERIOD_TO_NOTE = dict(zip(PERIODS, NOTE_NAMES))
NOTE_TO_PERIOD = dict(zip(NOTE_NAMES, PERIODS))

EFFECT_CHARS = "0123456789ABCDEF"
SIGNATURES = {"M.K.", "M!K!", "4CHN", "FLT4"}


@dataclass(frozen=True)
class Cell:
    period: int = 0
    sample: int = 0
    effect: int = 0
    param: int = 0


@dataclass(frozen=True)
class Sample:
    index: int
    name: str
    length: int
    finetune: int
    volume: int
    repeat_start: int
    repeat_length: int
    data: bytes
    offset: int

    @property
    def used(self) -> bool:
        return self.length > 0


@dataclass
class Module:
    title: str
    signature: str
    song_length: int
    restart: int
    order: list[int]
    samples: list[Sample]
    patterns: list[list[list[Cell]]]  # [pattern][row][channel]

    @property
    def used_patterns(self) -> list[int]:
        return sorted(set(self.order[: self.song_length]))

    def to_song(self, settings: RenderSettings | None = None) -> Song:
        """Convert a parsed MOD into the renderer's frontend-independent song."""
        instruments: dict[int, Instrument] = {}
        sample_data: dict[int, bytes] = {}
        for sample in self.samples:
            if not sample.used:
                continue
            instruments[sample.index] = Instrument(
                name=sample.name,
                length=sample.length,
                finetune=sample.finetune,
                volume=sample.volume,
                repeat_start=sample.repeat_start,
                repeat_length=sample.repeat_length,
            )
            sample_data[sample.index] = sample.data

        return Song(
            title=self.title,
            order=list(self.order[: self.song_length]),
            patterns=self.patterns,
            instruments=instruments,
            sample_data=sample_data,
            settings=settings or RenderSettings(),
        )


@dataclass(frozen=True)
class Instrument:
    name: str
    length: int
    finetune: int
    volume: int
    repeat_start: int
    repeat_length: int


@dataclass
class RenderSettings:
    tempo: float = 125.0
    speed: float = 6.0
    sample_rate: int = 22050
    orders: int = 8
    wav_path: Path = Path(DEFAULT_WAV)


@dataclass
class Song:
    title: str
    order: list[int]
    patterns: list[list[list[Cell]]]
    instruments: dict[int, Instrument]
    sample_data: dict[int, bytes]
    settings: RenderSettings


def parse_mod(path: Path | str) -> Module:
    """Parse a classic 4-channel Protracker module."""
    path = Path(path)
    raw = path.read_bytes()
    if len(raw) < 1084:
        raise ValueError(f"{path} is too short to be a classic MOD file")

    signature = raw[1080:1084].decode("ascii", errors="replace")
    if signature not in SIGNATURES:
        raise ValueError(f"Unsupported MOD signature {signature!r}; expected a 4-channel module")

    title = raw[0:20].split(b"\0", 1)[0].decode("latin-1", errors="replace").strip()
    song_length = raw[950]
    restart = raw[951]
    order = list(raw[952:1080])

    sample_headers: list[tuple[str, int, int, int, int, int]] = []
    for i in range(31):
        off = 20 + i * 30
        name = raw[off : off + 22].split(b"\0", 1)[0].decode("latin-1", errors="replace").strip()
        length_words = int.from_bytes(raw[off + 22 : off + 24], "big")
        finetune = raw[off + 24] & 0x0F
        volume = raw[off + 25]
        repeat_start_words = int.from_bytes(raw[off + 26 : off + 28], "big")
        repeat_len_words = int.from_bytes(raw[off + 28 : off + 30], "big")
        sample_headers.append(
            (name, length_words * 2, finetune, volume, repeat_start_words * 2, repeat_len_words * 2)
        )

    total_sample_bytes = sum(header[1] for header in sample_headers)
    pattern_bytes = len(raw) - 1084 - total_sample_bytes
    # Some MOD writers append a few padding bytes after the last sample. Use
    # the highest referenced pattern as a lower bound so trailing bytes do not
    # make an otherwise valid module unparsable.
    required_patterns = max(1, max(order[:song_length], default=0) + 1)
    if pattern_bytes < required_patterns * 1024:
        raise ValueError(
            f"Cannot infer pattern count: {pattern_bytes} pattern bytes "
            f"cannot hold {required_patterns} referenced patterns"
        )
    pattern_count = pattern_bytes // 1024
    if pattern_count < required_patterns:
        pattern_count = required_patterns
    trailing_bytes = pattern_bytes - pattern_count * 1024
    if trailing_bytes >= 1024:
        raise ValueError(
            f"Cannot infer pattern count: {pattern_bytes} pattern bytes "
            f"leaves {trailing_bytes} trailing bytes"
        )

    sample_offset = 1084 + pattern_count * 1024
    samples: list[Sample] = []
    for i, (name, length, finetune, volume, rep_start, rep_len) in enumerate(sample_headers, start=1):
        samples.append(
            Sample(
                index=i,
                name=name,
                length=length,
                finetune=finetune,
                volume=volume,
                repeat_start=rep_start,
                repeat_length=rep_len,
                data=raw[sample_offset : sample_offset + length],
                offset=sample_offset,
            )
        )
        sample_offset += length

    patterns: list[list[list[Cell]]] = []
    for p in range(pattern_count):
        base = 1084 + p * 1024
        rows: list[list[Cell]] = []
        for r in range(PATTERN_ROWS):
            channels: list[Cell] = []
            for c in range(CHANNEL_COUNT):
                off = base + (r * 4 + c) * 4
                a, b, cc, d = raw[off : off + 4]
                period = ((a & 0x0F) << 8) | b
                sample_no = ((a & 0xF0) | (cc >> 4)) & 0x3F
                effect = cc & 0x0F
                channels.append(Cell(period, sample_no, effect, d))
            rows.append(channels)
        patterns.append(rows)

    return Module(
        title=title or "untitled",
        signature=signature,
        song_length=song_length,
        restart=restart,
        order=order,
        samples=samples,
        patterns=patterns,
    )


def format_cell(cell: Cell) -> str:
    """Render a cell as spreadsheet text, e.g. ``C-3 07 C40``."""
    note = PERIOD_TO_NOTE.get(cell.period, "---") if cell.period else "---"
    sample = f"{cell.sample:02d}" if cell.sample else "--"
    effect = f"{EFFECT_CHARS[cell.effect]}{cell.param:02X}" if (cell.effect or cell.param) else "..."
    return f"{note} {sample} {effect}"


def parse_cell(text: object) -> Cell:
    """Parse spreadsheet cell text such as ``C-3 07 C40`` or ``--- -- ...``."""
    parts = str(text or "").split()
    if len(parts) < 3:
        return Cell()

    period = NOTE_TO_PERIOD.get(parts[0].upper(), 0)

    sample = 0
    if parts[1].isdigit():
        sample = int(parts[1])

    effect = 0
    param = 0
    code = parts[2]
    if len(code) >= 3 and all(ch.upper() in EFFECT_CHARS for ch in code[:3]):
        effect = int(code[0], 16)
        param = int(code[1:3], 16)

    return Cell(period, sample, effect, param)
