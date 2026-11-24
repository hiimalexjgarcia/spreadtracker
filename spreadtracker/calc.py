"""LibreOffice Calc integration for Spreadtracker.

The functions here accept a UNO spreadsheet document and read the same
worksheet layout generated for Excel. They use only the Python standard
library plus the shared ``spreadtracker`` renderer, so they work in Calc's
Python macro environment.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from .audio import play_wav, stop_playback as _stop_playback
from .model import (
    CHANNEL_COUNT,
    DEFAULT_WAV,
    INSTRUMENT_FIRST_ROW,
    ORDER_FIRST_ROW,
    PATTERN_BLOCK_ROWS,
    PATTERN_FIRST_DATA_ROW,
    PATTERN_ROWS,
    Instrument,
    RenderSettings,
    Song,
    parse_cell,
)
from .render import render_song

STATUS_CELL = (3, 2)  # StartHere!D3, zero-based (column, row)


def _sheets(doc):
    return doc.Sheets if hasattr(doc, "Sheets") else doc.getSheets()


def _sheet(doc, name: str):
    sheets = _sheets(doc)
    if hasattr(sheets, "getByName"):
        return sheets.getByName(name)
    return sheets[name]


def _cell(sheet, column: int, row: int):
    return sheet.getCellByPosition(column, row)


def _cell_string(sheet, column: int, row: int) -> str:
    return str(_cell(sheet, column, row).getString() or "")


def _cell_number(sheet, column: int, row: int) -> float:
    return float(_cell(sheet, column, row).getValue() or 0.0)


def _set_status(doc, text: str) -> None:
    try:
        sheet = _sheet(doc, "StartHere")
        cell = _cell(sheet, *STATUS_CELL)
        if hasattr(cell, "setString"):
            cell.setString(text)
    except Exception:
        pass


def _document_directory(doc) -> Path:
    url = ""
    try:
        url = str(doc.getURL() or "")
    except Exception:
        pass

    if url.startswith("file:"):
        path = unquote(urlparse(url).path)
        if os.name == "nt" and path.startswith("/"):
            path = path[1:]
        parent = Path(path).parent
        if str(parent):
            return parent
    return Path.cwd()


def read_settings(doc) -> RenderSettings:
    song = _sheet(doc, "Song")
    wav_text = _cell_string(song, 1, 9).strip() or DEFAULT_WAV
    return RenderSettings(
        tempo=_cell_number(song, 1, 5) or 125.0,
        speed=_cell_number(song, 1, 6) or 6.0,
        sample_rate=int(_cell_number(song, 1, 7) or 22050),
        orders=int(_cell_number(song, 1, 8) or 8),
        wav_path=Path(wav_text),
    )


def resolve_wav_path(doc, settings: RenderSettings) -> Path:
    path = Path(settings.wav_path)
    if not path.is_absolute():
        path = _document_directory(doc) / path
    return path


def read_order(doc) -> list[int]:
    song = _sheet(doc, "Song")
    count = int(_cell_number(song, 1, 11))  # Song!B12
    count = max(0, min(count, 128))
    return [int(_cell_number(song, 1, ORDER_FIRST_ROW - 1 + i)) for i in range(count)]


def read_pattern(doc, pattern_index: int) -> list[list]:
    sheet = _sheet(doc, "Patterns")
    base_row = PATTERN_FIRST_DATA_ROW - 1 + pattern_index * PATTERN_BLOCK_ROWS
    rows: list[list] = []
    for row in range(PATTERN_ROWS):
        rows.append(
            [
                parse_cell(_cell_string(sheet, 1 + channel, base_row + row))
                for channel in range(CHANNEL_COUNT)
            ]
        )
    return rows


def read_instruments(doc) -> dict[int, Instrument]:
    sheet = _sheet(doc, "Instruments")
    instruments: dict[int, Instrument] = {}
    for sample_no in range(1, 32):
        row = INSTRUMENT_FIRST_ROW - 1 + (sample_no - 1)
        name = _cell_string(sheet, 1, row)
        length = int(_cell_number(sheet, 2, row))
        if not name and length == 0:
            continue
        instruments[sample_no] = Instrument(
            name=name,
            length=length,
            finetune=int(_cell_number(sheet, 3, row)),
            volume=int(_cell_number(sheet, 4, row)),
            repeat_start=int(_cell_number(sheet, 5, row)),
            repeat_length=int(_cell_number(sheet, 6, row)),
        )
    return instruments


def read_sample_data(doc) -> dict[int, bytes]:
    sheet = _sheet(doc, "SampleData")
    chunks: dict[int, list[str]] = {}
    row = 1  # skip the header row
    while row < 20000:
        sample_text = _cell_string(sheet, 0, row).strip()
        if not sample_text:
            break
        try:
            sample_no = int(float(sample_text))
        except ValueError:
            break
        chunks.setdefault(sample_no, []).append(_cell_string(sheet, 2, row))
        row += 1

    return {
        sample_no: base64.b64decode("".join(parts), validate=False)
        for sample_no, parts in chunks.items()
    }


def load_song(doc) -> Song:
    """Build a renderer-ready song from the open Calc document."""
    settings = read_settings(doc)
    order = read_order(doc)
    pattern_count = max(order) + 1 if order else 0
    title = _cell_string(_sheet(doc, "Song"), 1, 3) or "untitled"

    return Song(
        title=title,
        order=order,
        patterns=[read_pattern(doc, index) for index in range(pattern_count)],
        instruments=read_instruments(doc),
        sample_data=read_sample_data(doc),
        settings=settings,
    )


def render_wav(doc, out_path: str | Path | None = None) -> str:
    """Render the open workbook to WAV and return the full path."""
    song = load_song(doc)
    song.settings.wav_path = (
        Path(out_path) if out_path is not None else resolve_wav_path(doc, song.settings)
    )
    target = render_song(song, song.settings.wav_path)
    _set_status(doc, f"Rendered: {target}")
    return str(target)


def render_and_play(doc) -> str:
    """Render and start playback. Returns a human-readable status string."""
    try:
        target = render_wav(doc)
    except Exception as exc:
        message = f"Render failed: {exc}"
        _set_status(doc, message)
        return message

    if play_wav(target):
        message = f"Playing: {target}"
    else:
        message = f"Rendered, but no playback backend available: {target}"
    _set_status(doc, message)
    return message


def stop_playback() -> None:
    """Stop playback started by :func:`render_and_play`."""
    _stop_playback()
