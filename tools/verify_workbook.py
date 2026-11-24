#!/usr/bin/env python3
"""Sanity-check the generated workbook against its source MOD file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.build_workbook import format_cell, parse_mod


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mod", type=Path, default=Path("tests/fixtures/space_debris.mod"))
    parser.add_argument("--workbook", type=Path, default=Path("workbook/spreadtracker.xlsx"))
    args = parser.parse_args()

    mod = parse_mod(args.mod)
    wb = load_workbook(args.workbook, data_only=False)

    expected_sheets = ["StartHere", "Song", "Patterns", "Instruments", "Effects", "SampleData"]
    assert wb.sheetnames == expected_sheets, wb.sheetnames
    assert wb["SampleData"].sheet_state == "hidden"

    song = wb["Song"]
    assert song["B4"].value == mod.title
    assert song["B5"].value == mod.signature
    assert song["B12"].value == mod.song_length
    assert song["B14"].value == len(mod.patterns)
    assert song["A23"].value == 0
    assert song["B23"].value == mod.order[0]

    required_names = {
        "TempoBPM", "SpeedTicks", "SampleRateHz", "RenderOrders",
        "WavOutPath", "ModSourcePath", "SongLength", "OrderTable",
        "InstrumentTable", "SampleDataTable",
    }
    assert required_names.issubset(set(wb.defined_names)), sorted(required_names - set(wb.defined_names))

    patterns = wb["Patterns"]
    assert str(patterns["A4"].value).startswith("PATTERN 00")
    assert patterns["B6"].value == format_cell(mod.patterns[0][0][0])
    assert patterns["E6"].value == format_cell(mod.patterns[0][0][3])

    instruments = wb["Instruments"]
    assert instruments["B4"].value == mod.samples[0].name
    assert instruments["C4"].value == mod.samples[0].length

    assert wb["SampleData"]["A2"].value == 1
    assert wb["SampleData"]["C2"].value

    print(
        f"OK: {args.workbook} matches {args.mod} "
        f"({len(mod.patterns)} patterns, {mod.song_length} orders)."
    )


if __name__ == "__main__":
    main()
