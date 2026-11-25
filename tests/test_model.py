"""Parsing checks for classic MOD edge cases."""

from __future__ import annotations

import unittest
from pathlib import Path

from spreadtracker.model import parse_mod

ROOT = Path(__file__).resolve().parent.parent


class ModelTests(unittest.TestCase):
    def test_trailing_padding_does_not_break_pattern_count(self) -> None:
        # ELYSIUM.MOD has four padding bytes after its last sample. Classic MOD
        # sample lengths are even, so the old strict size check rejected it.
        module = parse_mod(ROOT / "tests" / "fixtures" / "elysium.mod")

        self.assertEqual(module.signature, "M.K.")
        self.assertEqual(module.song_length, 29)
        self.assertEqual(len(module.patterns), 23)
        self.assertEqual(max(module.order[: module.song_length]), 22)
        self.assertEqual(len(module.samples[23].data), 26152)
        self.assertEqual(module.samples[23].repeat_length, 7330)


if __name__ == "__main__":
    unittest.main()
