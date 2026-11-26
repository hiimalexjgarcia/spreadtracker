"""Verify the committed pt2-clone reference WAV fixtures."""

from __future__ import annotations

import hashlib
import sys
import unittest
import wave
from array import array
from pathlib import Path

from tools.compare_pt2_render import REFERENCE_DIR, load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULES = sorted((REPO_ROOT / "tests" / "fixtures").glob("*.mod"))


class Pt2ReferenceFixtureTests(unittest.TestCase):
    def test_manifest_matches_committed_wavs(self) -> None:
        manifest = load_manifest(REFERENCE_DIR)
        self.assertEqual(manifest.get("sample_rate"), 22050)
        self.assertEqual(manifest.get("amiga_model"), "A1200")
        self.assertEqual(manifest.get("channels"), 1)
        self.assertEqual(manifest.get("sample_width"), 2)
        self.assertEqual(set(manifest.get("files", {})), {mod.name for mod in MODULES})

        for mod in MODULES:
            with self.subTest(module=mod.name):
                wav_path = REFERENCE_DIR / f"{mod.stem}.wav"
                entry = manifest["files"][mod.name]
                with wave.open(str(wav_path), "rb") as wav:
                    self.assertEqual(wav.getframerate(), 22050)
                    self.assertEqual(wav.getnchannels(), 1)
                    self.assertEqual(wav.getsampwidth(), 2)
                    self.assertEqual(wav.getnframes(), entry["frames"])

                digest = hashlib.sha256(wav_path.read_bytes()).hexdigest()
                self.assertEqual(digest, entry["sha256"])

                peak = 0
                with wave.open(str(wav_path), "rb") as wav:
                    while True:
                        data = wav.readframes(wav.getframerate())
                        if not data:
                            break
                        samples = array("h")
                        samples.frombytes(data)
                        if sys.byteorder == "big":
                            samples.byteswap()
                        peak = max(peak, max(abs(value) for value in samples))
                self.assertGreater(peak, 0)


if __name__ == "__main__":
    unittest.main()
