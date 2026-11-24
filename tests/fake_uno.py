"""Minimal UNO-shaped document backed by openpyxl, for headless tests."""

from __future__ import annotations

from pathlib import Path

from openpyxl import load_workbook


class FakeCell:
    def __init__(self, value=None):
        self.value = value

    def getString(self) -> str:
        if self.value is None:
            return ""
        return str(self.value)

    def setString(self, value: str) -> None:
        self.value = value

    def getValue(self) -> float:
        if self.value is None or self.value == "":
            return 0.0
        if isinstance(self.value, str) and self.value.startswith("="):
            return 0.0
        try:
            return float(self.value)
        except (TypeError, ValueError):
            return 0.0


class FakeSheet:
    def __init__(self, name: str):
        self.name = name
        self.cells: dict[tuple[int, int], object] = {}

    def getCellByPosition(self, column: int, row: int) -> FakeCell:
        return FakeCell(self.cells.get((column, row)))


class FakeSheets:
    def __init__(self):
        self._sheets: dict[str, FakeSheet] = {}

    def getByName(self, name: str) -> FakeSheet:
        return self._sheets[name]

    def add(self, sheet: FakeSheet) -> None:
        self._sheets[sheet.name] = sheet


class FakeDocument:
    """Enough of a UNO spreadsheet document for spreadtracker.calc."""

    def __init__(self, workbook_path: Path | str):
        workbook_path = Path(workbook_path)
        wb = load_workbook(workbook_path, data_only=False)
        self.Sheets = FakeSheets()

        for worksheet in wb.worksheets:
            sheet = FakeSheet(worksheet.title)
            for row in worksheet.iter_rows():
                for cell in row:
                    if cell.value is not None:
                        sheet.cells[(cell.column - 1, cell.row - 1)] = cell.value
            self.Sheets.add(sheet)

        self._url = workbook_path.resolve().as_uri()

    def getURL(self) -> str:
        return self._url
