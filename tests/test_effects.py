"""Focused checks for tick-driven MOD effects in the shared renderer."""

from __future__ import annotations

import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from spreadtracker.model import Cell, Instrument, RenderSettings, Song
from spreadtracker.render import Voice, advance_voice_tick, render_song, wave_value


SAMPLE_DATA = bytes([0x40] * 64)
NOTE = Cell(period=428, sample=1, effect=0, param=0)


def make_song(
    rows: list[list[Cell]],
    *,
    volume: int = 64,
    tempo: float = 125.0,
    speed: float = 6.0,
    sample_rate: int = 8000,
    order: list[int] | None = None,
    patterns: list[list[list[Cell]]] | None = None,
    orders: int = 1,
) -> Song:
    return Song(
        title="effect-test",
        order=order if order is not None else [0],
        patterns=patterns if patterns is not None else [rows],
        instruments={
            1: Instrument(
                name="test",
                length=len(SAMPLE_DATA),
                finetune=0,
                volume=volume,
                repeat_start=0,
                repeat_length=2,
            )
        },
        sample_data={1: SAMPLE_DATA},
        settings=RenderSettings(
            tempo=tempo,
            speed=speed,
            sample_rate=sample_rate,
            orders=orders,
        ),
    )


def empty_rows(count: int = 64) -> list[list[Cell]]:
    return [[Cell() for _ in range(4)] for _ in range(count)]


def render_frames(song: Song, path: Path) -> tuple[int, list[float]]:
    render_song(song, path)
    with wave.open(str(path), "rb") as wav:
        frames = wav.getnframes()
        pcm = array("h")
        pcm.frombytes(wav.readframes(frames))
    return frames, [sample / 32768.0 for sample in pcm]


class EffectTests(unittest.TestCase):
    def test_fxx_speed_changes_row_length(self) -> None:
        slow = empty_rows()
        slow[0][0] = Cell(effect=0x0F, param=0x06)
        fast = empty_rows()
        fast[0][0] = Cell(effect=0x0F, param=0x0F)

        with tempfile.TemporaryDirectory() as temp_dir:
            slow_frames, _ = render_frames(make_song(slow), Path(temp_dir) / "slow.wav")
            fast_frames, _ = render_frames(make_song(fast), Path(temp_dir) / "fast.wav")

        self.assertAlmostEqual(fast_frames / slow_frames, 15 / 6, delta=0.05)

    def test_fxx_tempo_changes_tick_length(self) -> None:
        slow = empty_rows()
        slow[0][0] = Cell(effect=0x0F, param=0x20)  # 32 BPM
        fast = empty_rows()
        fast[0][0] = Cell(effect=0x0F, param=0x40)  # 64 BPM

        with tempfile.TemporaryDirectory() as temp_dir:
            slow_frames, _ = render_frames(make_song(slow), Path(temp_dir) / "slow.wav")
            fast_frames, _ = render_frames(make_song(fast), Path(temp_dir) / "fast.wav")

        self.assertAlmostEqual(slow_frames / fast_frames, 2.0, delta=0.05)

    def test_tone_portamento_moves_period_gradually(self) -> None:
        voice = Voice(active=True, period=400.0, target_period=300.0, port_speed=8)
        advance_voice_tick(voice)
        self.assertEqual(voice.period, 392.0)
        for _ in range(9):
            advance_voice_tick(voice)
        self.assertEqual(voice.period, 320.0)
        for _ in range(10):
            advance_voice_tick(voice)
        self.assertEqual(voice.period, 300.0)

    def test_vibrato_phase_and_waveforms(self) -> None:
        voice = Voice(active=True, period=428.0, vib_speed=4, vib_depth=3)
        advance_voice_tick(voice)
        self.assertEqual(voice.vib_pos, 4)
        self.assertNotEqual(wave_value(0), wave_value(8))
        self.assertEqual(wave_value(0, 2), -128)
        self.assertEqual(wave_value(16, 2), 127)

    def test_volume_slide_uses_one_step_per_tick(self) -> None:
        voice = Voice(active=True, volume=0.5, slide_down=1)
        advance_voice_tick(voice)
        self.assertAlmostEqual(voice.volume, 0.5 - 1 / 64)

    def test_fine_volume_slide_lowers_rendered_level(self) -> None:
        rows = empty_rows(2)
        rows[0][0] = NOTE
        rows[1][0] = Cell(effect=0x0E, param=0xB1)  # EB1
        rows_no_slide = empty_rows(2)
        rows_no_slide[0][0] = NOTE

        with tempfile.TemporaryDirectory() as temp_dir:
            _, with_slide = render_frames(make_song(rows), Path(temp_dir) / "slide.wav")
            _, without_slide = render_frames(make_song(rows_no_slide), Path(temp_dir) / "plain.wav")

        rms_slide = sum(value * value for value in with_slide) / len(with_slide)
        rms_plain = sum(value * value for value in without_slide) / len(without_slide)
        self.assertLess(rms_slide, rms_plain)

    def test_pattern_break_shortens_render(self) -> None:
        rows = empty_rows()
        rows[0][0] = Cell(effect=0x0D, param=0x20)  # break to row 20 hex
        plain = empty_rows()

        with tempfile.TemporaryDirectory() as temp_dir:
            break_frames, _ = render_frames(make_song(rows), Path(temp_dir) / "break.wav")
            plain_frames, _ = render_frames(make_song(plain), Path(temp_dir) / "plain.wav")

        self.assertLess(break_frames, plain_frames // 4)

    def test_zero_sample_volume_means_full_volume(self) -> None:
        rows = empty_rows(2)
        rows[0][0] = NOTE
        rows[1][0] = Cell(effect=0x0C, param=0x40)

        with tempfile.TemporaryDirectory() as temp_dir:
            _, zero_volume = render_frames(
                make_song(rows, volume=0), Path(temp_dir) / "zero.wav"
            )
            _, full_volume = render_frames(
                make_song(rows, volume=64), Path(temp_dir) / "full.wav"
            )

        self.assertEqual(zero_volume, full_volume)

    def test_5xx_combines_volume_slide_and_portamento(self) -> None:
        rows = empty_rows(2)
        rows[0][0] = NOTE
        rows[1][0] = Cell(effect=0x05, param=0x01)  # slide down, hold portamento
        plain = empty_rows(2)
        plain[0][0] = NOTE

        with tempfile.TemporaryDirectory() as temp_dir:
            _, with_slide = render_frames(make_song(rows), Path(temp_dir) / "slide5.wav")
            _, without_slide = render_frames(make_song(plain), Path(temp_dir) / "plain.wav")

        rms_slide = sum(value * value for value in with_slide) / len(with_slide)
        rms_plain = sum(value * value for value in without_slide) / len(without_slide)
        self.assertLess(rms_slide, rms_plain)

    def test_ea_fine_volume_slide_up_increases_rendered_level(self) -> None:
        rows = empty_rows(2)
        rows[0][0] = NOTE
        rows[1][0] = Cell(effect=0x0E, param=0xAF)  # EAF
        plain = empty_rows(2)
        plain[0][0] = NOTE

        with tempfile.TemporaryDirectory() as temp_dir:
            _, with_slide = render_frames(
                make_song(rows, volume=32), Path(temp_dir) / "slide.wav"
            )
            _, without_slide = render_frames(
                make_song(plain, volume=32), Path(temp_dir) / "plain.wav"
            )

        rms_slide = sum(value * value for value in with_slide) / len(with_slide)
        rms_plain = sum(value * value for value in without_slide) / len(without_slide)
        self.assertGreater(rms_slide, rms_plain)

    def test_ec_note_cut_silences_channel(self) -> None:
        rows = empty_rows(2)
        rows[0][0] = NOTE
        rows[1][0] = Cell(effect=0x0E, param=0xC1)  # EC1: cut after one tick
        plain = empty_rows(2)
        plain[0][0] = NOTE

        with tempfile.TemporaryDirectory() as temp_dir:
            _, with_cut = render_frames(make_song(rows), Path(temp_dir) / "cut.wav")
            _, without_cut = render_frames(make_song(plain), Path(temp_dir) / "plain.wav")

        rms_cut = sum(value * value for value in with_cut) / len(with_cut)
        rms_plain = sum(value * value for value in without_cut) / len(without_cut)
        self.assertLess(rms_cut, rms_plain / 10)

    def test_ee_pattern_delay_extends_row(self) -> None:
        rows = empty_rows(2)
        rows[0][0] = Cell(effect=0x0E, param=0xE1)  # EE1, global even when silent
        plain = empty_rows(2)

        with tempfile.TemporaryDirectory() as temp_dir:
            delayed_frames, _ = render_frames(make_song(rows), Path(temp_dir) / "delay.wav")
            plain_frames, _ = render_frames(make_song(plain), Path(temp_dir) / "plain.wav")

        self.assertGreater(delayed_frames, plain_frames + 100)

    def test_bxx_jumps_at_end_of_pattern(self) -> None:
        jump_rows = empty_rows()
        jump_rows[0][0] = Cell(effect=0x0B, param=0x00)  # back to order 0
        note_rows = empty_rows()
        note_rows[0][0] = NOTE
        plain_rows = empty_rows()

        with tempfile.TemporaryDirectory() as temp_dir:
            jump_song = make_song(
                jump_rows,
                order=[0, 1],
                patterns=[jump_rows, note_rows],
                orders=2,
            )
            plain_song = make_song(
                plain_rows,
                order=[0, 1],
                patterns=[plain_rows, note_rows],
                orders=2,
            )
            _, jumped = render_frames(jump_song, Path(temp_dir) / "jump.wav")
            _, plain = render_frames(plain_song, Path(temp_dir) / "plain.wav")

        rms_jumped = sum(value * value for value in jumped) / len(jumped)
        rms_plain = sum(value * value for value in plain) / len(plain)
        self.assertLess(rms_jumped, rms_plain / 10)

    def test_7xx_tremolo_modulates_volume(self) -> None:
        rows = empty_rows(2)
        rows[0][0] = NOTE
        rows[1][0] = Cell(effect=0x07, param=0x4F)  # speed 4, depth 15
        plain = empty_rows(2)
        plain[0][0] = NOTE

        with tempfile.TemporaryDirectory() as temp_dir:
            _, with_tremolo = render_frames(
                make_song(rows, volume=32), Path(temp_dir) / "tremolo.wav"
            )
            _, without_tremolo = render_frames(
                make_song(plain, volume=32), Path(temp_dir) / "plain.wav"
            )

        difference = sum(
            abs(left - right) for left, right in zip(with_tremolo, without_tremolo)
        ) / len(with_tremolo)
        self.assertGreater(difference, 1e-4)


if __name__ == "__main__":
    unittest.main()
