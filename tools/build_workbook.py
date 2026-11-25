#!/usr/bin/env python3
"""Build the Spreadtracker workbook from a classic 4-channel .mod file.

The workbook is a *data/UI prototype*: it imports the module's order list,
patterns and sample headers into worksheets, embeds sample bytes as base64 in
a hidden sheet, and ships VBA source files separately for import into a
macro-enabled workbook.

Usage:
    python tools/build_workbook.py \
        --mod tests/fixtures/space_debris.mod \
        --out workbook/spreadtracker.xlsx
"""

from __future__ import annotations

import argparse
import base64
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.hyperlink import Hyperlink

from spreadtracker.model import (
    BASE64_CHUNK_CHARS,
    INSTRUMENT_FIRST_ROW,
    ORDER_FIRST_ROW,
    PATTERN_BLOCK_ROWS,
    PATTERN_FIRST_DATA_ROW,
    PATTERN_FIRST_HEADER_ROW,
    Cell,
    Module,
    Sample,
    format_cell,
    parse_mod,
)

PROJECT_NAME = "Spreadtracker"
DEFAULT_WORKBOOK = Path("workbook/spreadtracker.xlsx")
DEFAULT_WAV = "spreadtracker.wav"

EFFECT_INFO: tuple[tuple[str, str, str, str], ...] = (
    ("0", "Arpeggio", "Fast three-note arpeggio: 0XY plays note, note+X, note+Y semitones.", "Rendered"),
    ("1", "Portamento up", "Slide sample playback rate upward.", "Rendered"),
    ("2", "Portamento down", "Slide sample playback rate downward.", "Rendered"),
    ("3", "Tone portamento", "Slide toward the new note period.", "Rendered"),
    ("4", "Vibrato", "Period modulation with speed/depth parameters.", "Rendered"),
    ("5", "Volume slide + portamento", "Combines effects A and 3.", "Rendered"),
    ("6", "Volume slide + vibrato", "Combines effects A and 4.", "Rendered"),
    ("7", "Tremolo", "Volume modulation.", "Rendered"),
    ("8", "Set panning", "Protracker-specific pan command (inaudible in mono).", "Parsed only"),
    ("9", "Sample offset", "Start sample at offset XX00 bytes.", "Rendered"),
    ("A", "Volume slide", "Up/down volume slide: X=up, Y=down.", "Rendered"),
    ("B", "Pattern jump", "Jump to pattern XX at the end of the current pattern.", "Rendered"),
    ("C", "Set volume", "Set channel volume to XX (0-64).", "Rendered"),
    ("D", "Pattern break", "Break to row XX of the next pattern.", "Rendered"),
    ("E", "Extended", "Subcommands E0-EF; see rows below.", "Partial"),
    ("F", "Set speed / tempo", "F01-F1F set speed; F20-FF set BPM.", "Rendered"),
)

EFFECT_SUB_INFO: tuple[tuple[str, str, str, str], ...] = (
    ("E0", "Filter on/off", "Amiga hardware filter control.", "Parsed only"),
    ("E1", "Fine portamento up", "Small upward pitch slide.", "Rendered"),
    ("E2", "Fine portamento down", "Small downward pitch slide.", "Rendered"),
    ("E3", "Glissando", "Force portamento to chromatic steps.", "Parsed only"),
    ("E4", "Set vibrato waveform", "0=sine, 1=ramp down, 2=square, 3=ramp up.", "Rendered"),
    ("E5", "Set finetune", "Override sample finetune.", "Rendered"),
    ("E6", "Pattern loop", "Loop a pattern section.", "Parsed only"),
    ("E7", "Set tremolo waveform", "Waveform select for tremolo.", "Rendered"),
    ("E8", "Set panning", "Set channel pan position.", "Parsed only"),
    ("E9", "Retrigger note", "Retrigger the current sample.", "Rendered"),
    ("EA", "Fine volume slide up", "Small volume increase.", "Rendered"),
    ("EB", "Fine volume slide down", "Small volume decrease.", "Rendered"),
    ("EC", "Note cut", "Cut the note after X ticks.", "Rendered"),
    ("ED", "Note delay", "Delay the note by X ticks.", "Parsed only"),
    ("EE", "Pattern delay", "Repeat the row X times.", "Rendered"),
    ("EF", "Invert loop", "Amiga sample loop inversion.", "Parsed only"),
)

HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
SUBHEADER_FILL = PatternFill("solid", fgColor="D9E2F3")
BLOCK_FILL = PatternFill("solid", fgColor="2F5597")
TITLE_FONT = Font(bold=True, size=14, color="FFFFFF")
HEADER_FONT = Font(bold=True, color="FFFFFF")
MONO_FONT = Font(name="Consolas", size=10)
LABEL_FONT = Font(bold=True)
THIN = Side(style="thin", color="B7C9E2")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

EFFECT_FILLS: dict[int, PatternFill] = {
    0x0: PatternFill("solid", fgColor="DDEBF7"),
    0x1: PatternFill("solid", fgColor="E4DFEC"),
    0x2: PatternFill("solid", fgColor="E4DFEC"),
    0x3: PatternFill("solid", fgColor="E4DFEC"),
    0x4: PatternFill("solid", fgColor="E4DFEC"),
    0x5: PatternFill("solid", fgColor="FCE4D6"),
    0x6: PatternFill("solid", fgColor="FCE4D6"),
    0x7: PatternFill("solid", fgColor="FCE4D6"),
    0x8: PatternFill("solid", fgColor="E2EFDA"),
    0x9: PatternFill("solid", fgColor="F2F2F2"),
    0xA: PatternFill("solid", fgColor="FCE4D6"),
    0xB: PatternFill("solid", fgColor="FFC7CE"),
    0xC: PatternFill("solid", fgColor="E2EFDA"),
    0xD: PatternFill("solid", fgColor="FFC7CE"),
    0xE: PatternFill("solid", fgColor="FFF2CC"),
    0xF: PatternFill("solid", fgColor="E2EFDA"),
}


def style_title(ws, cell_range: str, text: str) -> None:
    ws.merge_cells(cell_range)
    cell = ws[cell_range.split(":")[0]]
    cell.value = text
    cell.fill = HEADER_FILL
    cell.font = TITLE_FONT
    cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[cell.row].height = 24


def style_header_row(ws, row: int, first_col: int, last_col: int) -> None:
    for col in range(first_col, last_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER


def add_defined_name(wb: Workbook, name: str, attr_text: str) -> None:
    wb.defined_names.add(DefinedName(name, attr_text=attr_text))


def build_start_sheet(wb: Workbook, mod: Module, out_path: Path) -> None:
    ws = wb.active
    ws.title = "StartHere"
    style_title(ws, "A1:F1", f"{PROJECT_NAME.upper()} — WORKBOOK PROTOTYPE")

    rows: list[tuple[str, str]] = [
        ("Source module", f"{mod.title}.mod  ·  {mod.signature}  ·  4 channels"),
        ("Generated workbook", out_path.name),
        ("Goal", "Use an Excel grid as a Protracker-style pattern editor, with VBA rendering audio to WAV."),
        ("Sheets", "Song = settings/order list · Patterns = pattern grid · Instruments = sample headers · Effects = command reference · SampleData = embedded PCM (hidden)."),
        ("To play (Excel)", "Import vba/TrackerCore.bas and vba/AudioRender.bas into the VBA editor, save as .xlsm, enable macros, and run RenderAndPlay."),
        ("To play (Calc)", "Copy calc/Spreadtracker.py and the spreadtracker package into LibreOffice's Scripts/python folder, open this workbook in Calc, and run RenderAndPlay. See README.md."),
        ("Current renderer", "Tick-driven renderer: note triggers, signed 8-bit samples, linear interpolation, one-shot handling for 2-byte MOD loop headers, arpeggio (0xx), portamento (1xx/2xx/3xx/5xx), vibrato (4xx/6xx), tremolo (7xx), sample offsets (9xx), volume slides (5xx/6xx/Axx), Bxx/Dxx navigation, Cxx volume, common E subcommands, and Fxx speed/tempo."),
        ("Timing", "Protracker timing: ticks/sec = BPM * 2 / 5. Rows advance once per Speed ticks; Fxx changes take effect immediately."),
        ("Sample numbers", "Shown as two-digit decimal values in the grid (not hexadecimal) to reduce ambiguity."),
        ("Rebuild", f"python tools/build_workbook.py --mod tests/fixtures/space_debris.mod --out {DEFAULT_WORKBOOK}"),
        ("Platform", "Playback requires Windows desktop Excel with macros enabled; Excel for the web cannot run VBA."),
    ]
    for i, (label, value) in enumerate(rows, start=3):
        ws.cell(row=i, column=1, value=label).font = LABEL_FONT
        ws.cell(row=i, column=2, value=value).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[i].height = 30 if len(value) > 90 else 18

    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 105
    ws.freeze_panes = "A3"


def build_song_sheet(wb: Workbook, mod: Module) -> None:
    ws = wb.create_sheet("Song")
    style_title(ws, "A1:D1", "SONG / PLAYBACK SETTINGS")

    headers = ["Setting", "Value", "Notes"]
    for col, text in enumerate(headers, start=1):
        ws.cell(row=3, column=col, value=text)
    style_header_row(ws, 3, 1, 3)

    used_samples = sum(1 for s in mod.samples if s.used)
    settings: list[tuple[str, object, str]] = [
        ("Title", mod.title, "From module header"),
        ("Signature", mod.signature, "Classic 4-channel Protracker module"),
        ("Tempo (BPM)", 125, "Named range: TempoBPM. Protracker default."),
        ("Speed (ticks/row)", 6, "Named range: SpeedTicks. Protracker default."),
        ("Output sample rate", 22050, "Named range: SampleRateHz. Lower rate keeps prototype renders small."),
        ("Render orders", min(8, mod.song_length), "Named range: RenderOrders. Increase to render more of the song."),
        ("WAV output path", DEFAULT_WAV, "Named range: WavOutPath. Relative paths resolve beside the workbook."),
        ("Source .mod path", "tests\\fixtures\\space_debris.mod", "Named range: ModSourcePath. Required only for sample bytes if SampleData is removed."),
        ("Song length (orders)", mod.song_length, "Named range: SongLength."),
        ("Restart position", mod.restart, "255 means no restart."),
        ("Pattern count", len(mod.patterns), "Inferred from file length and sample sizes."),
        ("Samples with data", used_samples, "31 sample slots exist in a classic MOD."),
        ("All-song duration (s)", "=B12*64*B7*5/(B6*2)", "Estimated from current tempo and speed."),
        ("Render duration (s)", "=B9*64*B7*5/(B6*2)", "Estimated for RenderOrders."),
        ("Render duration (mm:ss)", '=TEXT(B17/86400,"mm:ss")', "Convenience display."),
    ]
    for i, (label, value, note) in enumerate(settings, start=4):
        ws.cell(row=i, column=1, value=label).font = LABEL_FONT
        ws.cell(row=i, column=2, value=value)
        ws.cell(row=i, column=3, value=note).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=i, column=2).fill = SUBHEADER_FILL

    ws["B16"].number_format = "0.0"
    ws["B17"].number_format = "0.0"

    order_title_row = 21
    style_title(ws, f"A{order_title_row}:E{order_title_row}", "ORDER LIST — SEQUENCE OF PATTERNS")
    order_headers = ["Position", "Pattern", "Times used", "Sheet row", "Jump"]
    for col, text in enumerate(order_headers, start=1):
        ws.cell(row=order_title_row + 1, column=col, value=text)
    style_header_row(ws, order_title_row + 1, 1, 5)

    usage = Counter(mod.order[: mod.song_length])
    for i, pattern_index in enumerate(mod.order[: mod.song_length]):
        row = ORDER_FIRST_ROW + i
        cell_row = PATTERN_FIRST_HEADER_ROW + pattern_index * PATTERN_BLOCK_ROWS
        ws.cell(row=row, column=1, value=i)
        ws.cell(row=row, column=2, value=pattern_index)
        ws.cell(row=row, column=3, value=usage[pattern_index])
        ws.cell(row=row, column=4, value=cell_row)
        jump = ws.cell(row=row, column=5, value=f"Open P{pattern_index:02d}")
        jump.hyperlink = Hyperlink(
            ref=jump.coordinate,
            location=f"'Patterns'!A{cell_row}",
            display=jump.value,
            tooltip=f"Go to pattern {pattern_index:02d}",
        )
        jump.font = Font(color="0563C1", underline="single")
        if i % 2 == 1:
            for col in range(1, 6):
                ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor="F4F7FB")

    add_defined_name(wb, "TempoBPM", "Song!$B$6")
    add_defined_name(wb, "SpeedTicks", "Song!$B$7")
    add_defined_name(wb, "SampleRateHz", "Song!$B$8")
    add_defined_name(wb, "RenderOrders", "Song!$B$9")
    add_defined_name(wb, "WavOutPath", "Song!$B$10")
    add_defined_name(wb, "ModSourcePath", "Song!$B$11")
    add_defined_name(wb, "SongLength", "Song!$B$12")
    add_defined_name(wb, "OrderTable", f"Song!$A${ORDER_FIRST_ROW}:$E${ORDER_FIRST_ROW + mod.song_length - 1}")

    widths = {"A": 24, "B": 28, "C": 62, "D": 12, "E": 14}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"


def build_patterns_sheet(wb: Workbook, mod: Module) -> None:
    ws = wb.create_sheet("Patterns")
    style_title(ws, "A1:F1", f"PATTERNS — {len(mod.patterns)} PATTERNS, 64 ROWS, 4 CHANNELS")
    ws["A2"] = "Cell format: NOTE SAM EFF  ·  e.g. C-4 07 C40  ·  --- -- ... = empty  ·  sample numbers are decimal."
    ws["A3"] = "Each pattern block is 66 rows: header, column labels, then rows 00-3F. Effect colours follow the Effects sheet."
    ws["A2"].font = Font(italic=True, color="404040")
    ws["A3"].font = Font(italic=True, color="404040")

    for p, pattern in enumerate(mod.patterns):
        header_row = PATTERN_FIRST_HEADER_ROW + p * PATTERN_BLOCK_ROWS
        col_header_row = header_row + 1
        used_at = [i for i, v in enumerate(mod.order[: mod.song_length]) if v == p]
        label = f"PATTERN {p:02d}"
        if used_at:
            preview = ", ".join(str(v) for v in used_at[:10])
            if len(used_at) > 10:
                preview += ", ..."
            label += f"   ·   used at order positions: {preview}"

        ws.merge_cells(start_row=header_row, start_column=1, end_row=header_row, end_column=6)
        cell = ws.cell(row=header_row, column=1, value=label)
        cell.fill = BLOCK_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        ws.row_dimensions[header_row].height = 20

        for col, text in enumerate(["Row", "Ch 1", "Ch 2", "Ch 3", "Ch 4", "Time (s)"], start=1):
            ws.cell(row=col_header_row, column=col, value=text)
        style_header_row(ws, col_header_row, 1, 6)

        for r in range(64):
            out_row = col_header_row + 1 + r
            row_cell = ws.cell(row=out_row, column=1, value=f"{r:02X}")
            row_cell.font = MONO_FONT
            row_cell.alignment = Alignment(horizontal="center")
            if r % 4 == 0:
                row_cell.fill = PatternFill("solid", fgColor="E2EFDA")
            elif r % 2 == 1:
                row_cell.fill = PatternFill("solid", fgColor="F7F7F7")

            for c in range(4):
                note_cell = mod.patterns[p][r][c]
                value = format_cell(note_cell)
                target = ws.cell(row=out_row, column=2 + c, value=value)
                target.font = MONO_FONT
                target.alignment = Alignment(horizontal="center")
                target.border = BORDER
                if note_cell.effect or note_cell.param:
                    target.fill = EFFECT_FILLS[note_cell.effect]
                elif note_cell.period:
                    target.fill = PatternFill("solid", fgColor="FFF2CC")

            time_cell = ws.cell(
                row=out_row,
                column=6,
                value=f"=({r}*SpeedTicks*5)/(TempoBPM*2)",
            )
            time_cell.number_format = "0.000"
            time_cell.font = Font(size=9, color="808080")

    for col, width in {"A": 7, "B": 13, "C": 13, "D": 13, "E": 13, "F": 11}.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "B6"


def build_instruments_sheet(wb: Workbook, mod: Module) -> None:
    ws = wb.create_sheet("Instruments")
    style_title(ws, "A1:I1", "INSTRUMENTS / SAMPLE HEADERS")
    headers = [
        "#", "Name", "Bytes", "Finetune", "Volume",
        "Repeat start", "Repeat length", "Used", "Base64 chunks",
    ]
    for col, text in enumerate(headers, start=1):
        ws.cell(row=3, column=col, value=text)
    style_header_row(ws, 3, 1, len(headers))

    usage = Counter(
        cell.sample
        for pattern in mod.patterns
        for row in pattern
        for cell in row
        if cell.sample
    )
    for sample in mod.samples:
        row = INSTRUMENT_FIRST_ROW + sample.index - 1
        chunks = math.ceil(len(base64.b64encode(sample.data)) / BASE64_CHUNK_CHARS) if sample.used else 0
        values = [
            sample.index,
            sample.name,
            sample.length,
            sample.finetune,
            sample.volume,
            sample.repeat_start,
            sample.repeat_length,
            usage.get(sample.index, 0),
            chunks,
        ]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col, value=value)
            cell.border = BORDER
        if not sample.used:
            for col in range(1, len(headers) + 1):
                ws.cell(row=row, column=col).font = Font(color="A6A6A6")
        elif usage.get(sample.index, 0) == 0:
            ws.cell(row=row, column=8).fill = PatternFill("solid", fgColor="FFF2CC")

    add_defined_name(wb, "InstrumentTable", f"Instruments!$A$4:$I${INSTRUMENT_FIRST_ROW + 30}")
    for col, width in {"A": 5, "B": 24, "C": 12, "D": 10, "E": 9, "F": 13, "G": 14, "H": 8, "I": 14}.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"


def build_effects_sheet(wb: Workbook) -> None:
    ws = wb.create_sheet("Effects")
    style_title(ws, "A1:D1", "EFFECT COMMAND REFERENCE")
    headers = ["Code", "Name", "Description", "Renderer status"]
    for col, text in enumerate(headers, start=1):
        ws.cell(row=3, column=col, value=text)
    style_header_row(ws, 3, 1, 4)

    row = 4
    for code, name, description, status in EFFECT_INFO:
        for col, value in enumerate([code, name, description, status], start=1):
            ws.cell(row=row, column=col, value=value).border = BORDER
        ws.cell(row=row, column=1).font = Font(bold=True, name="Consolas")
        ws.cell(row=row, column=3).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 28
        row += 1

    row += 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    ws.cell(row=row, column=1, value="E-SUBCOMMANDS").fill = BLOCK_FILL
    ws.cell(row=row, column=1).font = HEADER_FONT
    row += 1
    for col, text in enumerate(["Code", "Name", "Description", "Renderer status"], start=1):
        ws.cell(row=row, column=col, value=text)
    style_header_row(ws, row, 1, 4)
    row += 1
    for code, name, description, status in EFFECT_SUB_INFO:
        for col, value in enumerate([code, name, description, status], start=1):
            ws.cell(row=row, column=col, value=value).border = BORDER
        ws.cell(row=row, column=1).font = Font(bold=True, name="Consolas")
        ws.cell(row=row, column=3).alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 26
        row += 1

    for col, width in {"A": 9, "B": 26, "C": 70, "D": 16}.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A4"


def build_sample_data_sheet(wb: Workbook, mod: Module) -> None:
    ws = wb.create_sheet("SampleData")
    for col, text in enumerate(["Sample", "Chunk", "Base64"], start=1):
        ws.cell(row=1, column=col, value=text)
    style_header_row(ws, 1, 1, 3)

    row = 2
    for sample in mod.samples:
        if not sample.used:
            continue
        encoded = base64.b64encode(sample.data).decode("ascii")
        for chunk_index, start in enumerate(range(0, len(encoded), BASE64_CHUNK_CHARS)):
            ws.cell(row=row, column=1, value=sample.index)
            ws.cell(row=row, column=2, value=chunk_index)
            ws.cell(row=row, column=3, value=encoded[start : start + BASE64_CHUNK_CHARS]).font = MONO_FONT
            row += 1

    add_defined_name(wb, "SampleDataTable", f"SampleData!$A$1:$C${max(row - 1, 1)}")
    for col, width in {"A": 9, "B": 9, "C": 105}.items():
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.sheet_state = "hidden"


def build_workbook(mod: Module, out_path: Path) -> None:
    wb = Workbook()
    build_start_sheet(wb, mod, out_path)
    build_song_sheet(wb, mod)
    build_patterns_sheet(wb, mod)
    build_instruments_sheet(wb, mod)
    build_effects_sheet(wb)
    build_sample_data_sheet(wb, mod)

    wb.properties.title = f"{PROJECT_NAME} prototype — {mod.title}"
    wb.properties.subject = f"{PROJECT_NAME} — a spreadsheet-based Protracker-style music tracker"
    wb.properties.description = "Generated by tools/build_workbook.py from a classic 4-channel MOD file."
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mod",
        type=Path,
        default=Path("tests/fixtures/space_debris.mod"),
        help="Path to a classic 4-channel Protracker module.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_WORKBOOK,
        help="Output .xlsx path.",
    )
    args = parser.parse_args()

    mod = parse_mod(args.mod)
    build_workbook(mod, args.out)
    print(
        f"Wrote {args.out}: {mod.title!r}, {mod.signature}, "
        f"{mod.song_length} orders, {len(mod.patterns)} patterns, "
        f"{sum(1 for s in mod.samples if s.used)} samples."
    )


if __name__ == "__main__":
    main()
