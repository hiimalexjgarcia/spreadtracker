"""Headless checks for the LibreOffice Calc data path and renderer."""

from __future__ import annotations

import tempfile
import unittest
import wave
from pathlib import Path

from spreadtracker import calc as calc_core
from spreadtracker.model import parse_cell

from tests.fake_uno import FakeDocument

ROOT = Path(__file__).resolve().parent.parent
WORKBOOK = ROOT / "workbook" / "spreadtracker.xlsx"


class CalcRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = FakeDocument(WORKBOOK)

    def test_parse_cell(self) -> None:
        cell = parse_cell("C-3 07 C40")
        self.assertGreater(cell.period, 0)
        self.assertEqual(cell.sample, 7)
        self.assertEqual(cell.effect, 0xC)
        self.assertEqual(cell.param, 0x40)
        self.assertEqual(parse_cell("--- -- ...").period, 0)

    def test_read_song_from_worksheets(self) -> None:
        song = calc_core.load_song(self.doc)
        self.assertEqual(song.title, "space_debris")
        self.assertEqual(song.order[:5], [1, 2, 3, 10, 17])
        self.assertEqual(len(song.patterns), 41)
        self.assertGreaterEqual(len(song.instruments), 17)
        self.assertGreaterEqual(len(song.sample_data), 17)
        self.assertEqual(len(song.sample_data[1]), song.instruments[1].length)

    def test_render_wav_from_calc_document(self) -> None:
        song_sheet = self.doc.Sheets.getByName("Song")
        song_sheet.cells[(1, 7)] = 8000   # B8: sample rate
        song_sheet.cells[(1, 8)] = 1      # B9: render orders
        # Pattern 0 uses samples with non-zero instrument volumes; pattern 1
        # relies on volume-slide effects that this prototype does not render.
        song_sheet.cells[(1, 22)] = 0     # B23: first order position

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "calc-test.wav"
            result = Path(calc_core.render_wav(self.doc, out_path=output))
            self.assertEqual(result, output)
            self.assertTrue(result.is_file())

            with wave.open(str(result), "rb") as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getsampwidth(), 2)
                self.assertEqual(wav.getframerate(), 8000)
                self.assertGreater(wav.getnframes(), 0)
                frames = wav.readframes(wav.getnframes())

            self.assertTrue(any(byte != 0 for byte in frames), "rendered WAV is silent")


if __name__ == "__main__":
    unittest.main()
