"""Checks for the pt2-clone comparison helper (no pt2 checkout required)."""

from __future__ import annotations

import sys
import tempfile
import unittest
import wave
from argparse import Namespace
from array import array
from pathlib import Path

from tools.compare_pt2_render import (
    Anchor,
    apply_headless_hook,
    best_lag,
    cached_reference,
    fit_drift,
    read_wav_mono,
    write_manifest,
    write_wav_mono,
)


def write_wav(path: Path, channels: int, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        values = array("h", samples)
        if sys.byteorder == "big":
            values.byteswap()
        wav.writeframes(values.tobytes())


class ComparePt2RenderTests(unittest.TestCase):
    def test_read_wav_mono_downmixes_stereo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "stereo.wav"
            write_wav(path, 2, [100, 200, 300, 400])
            self.assertEqual(list(read_wav_mono(path)), [150, 350])

    def test_write_wav_mono_writes_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "mono.wav"
            write_wav_mono(path, array("h", [111, -222]), 22050)

            with wave.open(str(path), "rb") as wav:
                self.assertEqual(wav.getnchannels(), 1)
                self.assertEqual(wav.getframerate(), 22050)
                self.assertEqual(list(read_wav_mono(path)), [111, -222])

    def test_best_lag_finds_shifted_window(self) -> None:
        pattern = [((index * 37) % 251) - 125 for index in range(160)]
        rendered = array("h", pattern)
        reference = array("h", [0] * 30 + pattern + [0] * 30)

        correlation, lag = best_lag(reference, rendered, 0, 120, 50, 1)

        self.assertEqual(lag, 30)
        self.assertGreater(correlation, 0.99)

    def test_fit_drift_recovers_linear_lag(self) -> None:
        anchors = [
            Anchor(frame=frame, lag=10 + round(frame * 0.001), correlation=1.0)
            for frame in (0, 100000, 200000, 300000)
        ]

        intercept, slope = fit_drift(anchors)

        self.assertAlmostEqual(intercept, 10.0, places=3)
        self.assertAlmostEqual(slope, 0.001, places=9)

    def test_apply_headless_hook_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir)
            main = source / "src" / "pt2_main.c"
            main.parent.mkdir()
            main.write_text(
                '#include "pt2_textedit.h"\n'
                "int main(void)\n"
                "{\n"
                "\tdisplayMainScreen();\n"
                "}\n",
                encoding="utf-8",
            )

            apply_headless_hook(source)
            first = main.read_text(encoding="utf-8")
            apply_headless_hook(source)
            second = main.read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertEqual(second.count('PT2_RENDER_OUT");'), 1)
            self.assertIn('#include "pt2_mod2wav.h"', second)
            self.assertLess(second.index("headless MOD2WAV"), second.index("displayMainScreen();"))

    def test_cached_reference_validates_settings_and_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reference_dir = Path(temp_dir)
            module = Path("demo.mod")
            wav_path = reference_dir / "demo.wav"
            write_wav(wav_path, 1, [123] * 64)
            manifest = write_manifest(
                reference_dir,
                Namespace(sample_rate=22050, amiga_model="A1200"),
                [module],
                [wav_path],
                None,
            )
            args = Namespace(sample_rate=22050, amiga_model="A1200")

            self.assertEqual(cached_reference(reference_dir, manifest, module, args), wav_path)

            args.sample_rate = 44100
            self.assertIsNone(cached_reference(reference_dir, manifest, module, args))

            args.sample_rate = 22050
            write_wav(wav_path, 1, [124] * 64)
            self.assertIsNone(cached_reference(reference_dir, manifest, module, args))


if __name__ == "__main__":
    unittest.main()
