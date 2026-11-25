"""Focused checks for tick-driven MOD effects in the shared renderer."""

from __future__ import annotations

import tempfile
import unittest
import wave
from array import array
from pathlib import Path

from spreadtracker.model import Cell, Instrument, RenderSettings, Song
from spreadtracker.render import (
    Voice,
    advance_voice_tick,
    effective_period,
    render_song,
    wave_value,
)


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
    sample_data: bytes = SAMPLE_DATA,
    repeat_length: int = len(SAMPLE_DATA),
) -> Song:
    return Song(
        title="effect-test",
        order=order if order is not None else [0],
        patterns=patterns if patterns is not None else [rows],
        instruments={
            1: Instrument(
                name="test",
                length=len(sample_data),
                finetune=0,
                volume=volume,
                repeat_start=0,
                repeat_length=repeat_length,
            )
        },
        sample_data={1: sample_data},
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
        # MOD/XM oscillator waveforms are 64 points long, so speed 4 repeats
        # after 16 tick advances rather than the old 8.
        for _ in range(15):
            advance_voice_tick(voice)
        self.assertEqual(voice.vib_pos, 0)
        self.assertEqual(wave_value(0), 0)
        self.assertEqual(wave_value(16), 127)
        self.assertEqual(wave_value(32), 0)
        self.assertEqual(wave_value(48), -127)
        self.assertEqual(wave_value(0, 2), 127)
        self.assertEqual(wave_value(32, 2), -127)

    def test_vibrato_uses_phase_before_current_tick_advance(self) -> None:
        voice = Voice(active=True, period=428.0, vib_speed=4, vib_depth=6)
        voice.vib_pos = 4  # stored phase after this tick's advance
        self.assertEqual(effective_period(voice, 0), 428.0)
        # Phase 0 is the sine table's zero crossing, not phase 4.
        self.assertEqual(effective_period(voice, 1), 428.0)

        voice.vib_pos = 16 + voice.vib_speed  # sine peak was at phase 16
        self.assertEqual(effective_period(voice, 1), 428.0 + (127 * 6) // 64)

    def test_volume_slide_uses_one_step_per_tick(self) -> None:
        voice = Voice(active=True, volume=0.5, slide_down=1)
        advance_voice_tick(voice)
        self.assertAlmostEqual(voice.volume, 0.5 - 1 / 64)

    def test_continuous_effects_run_once_per_tick_not_per_frame(self) -> None:
        rows = empty_rows(3)
        rows[0][0] = NOTE
        rows[1][0] = Cell(effect=0x0A, param=0x01)  # A01: slide down 1/tick

        with tempfile.TemporaryDirectory() as temp_dir:
            _, frames = render_frames(
                make_song(rows, sample_rate=8000), Path(temp_dir) / "slide.wav"
            )

        frames_per_tick = 8000 * 5 / (2 * 125)
        row_start = int(6 * frames_per_tick)
        first_tick = frames[row_start : row_start + int(frames_per_tick)]
        last_tick = frames[
            row_start + int(5 * frames_per_tick) : row_start + int(6 * frames_per_tick)
        ]
        first_rms = sum(value * value for value in first_tick) / len(first_tick)
        last_rms = sum(value * value for value in last_tick) / len(last_tick)

        self.assertGreater(first_rms, 0.01)
        # Five 1/64 steps should only reduce volume slightly. The old per-frame
        # bug muted the channel completely within the first tick.
        self.assertGreater(last_rms, first_rms / 2)

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

    def test_zero_sample_header_volume_is_silent_until_cxx(self) -> None:
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

        frames_per_row = int(6 * 8000 * 5 / (2 * 125))
        self.assertAlmostEqual(max(abs(value) for value in zero_volume[:frames_per_row]), 0.0)
        self.assertGreater(max(abs(value) for value in full_volume[:frames_per_row]), 0.1)
        # C40 on the next row must still be able to make the channel audible.
        self.assertGreater(
            max(abs(value) for value in zero_volume[frames_per_row : frames_per_row * 2]),
            0.1,
        )

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

    def test_bxx_on_final_order_ends_render(self) -> None:
        # ELYSIUM.MOD places B09 on the last row of its final order. It must
        # stop there, not loop back and render until the safety frame cap.
        jump_rows = empty_rows()
        jump_rows[63][0] = Cell(effect=0x0B, param=0x00)

        with tempfile.TemporaryDirectory() as temp_dir:
            jump_frames, _ = render_frames(
                make_song(
                    jump_rows,
                    order=[0, 1],
                    patterns=[empty_rows(), jump_rows],
                    orders=2,
                ),
                Path(temp_dir) / "final-jump.wav",
            )
            plain_frames, _ = render_frames(
                make_song(
                    empty_rows(),
                    order=[0, 1],
                    patterns=[empty_rows(), empty_rows()],
                    orders=2,
                ),
                Path(temp_dir) / "plain.wav",
            )

        self.assertGreater(jump_frames, plain_frames // 2)
        self.assertLess(jump_frames, plain_frames + 1000)

    def test_sample_only_cell_reloads_sample_volume(self) -> None:
        # A cell with a sample number but no note does not retrigger the sample,
        # but Protracker still reloads its header volume. musiklinjen.mod relies
        # on this while sliding channel volume down with A0F on every row.
        rows = empty_rows(2)
        rows[0][0] = Cell(period=428, sample=1, effect=0x0A, param=0x0F)
        rows[1][0] = Cell(sample=1, effect=0x0A, param=0x0F)
        plain = empty_rows(2)
        plain[0][0] = Cell(period=428, sample=1, effect=0x0A, param=0x0F)
        plain[1][0] = Cell(effect=0x0A, param=0x0F)

        with tempfile.TemporaryDirectory() as temp_dir:
            _, with_sample = render_frames(
                make_song(rows), Path(temp_dir) / "with-sample.wav"
            )
            _, without_sample = render_frames(
                make_song(plain), Path(temp_dir) / "without-sample.wav"
            )

        row_frames = int(8000 * 5.0 / (2.0 * 125.0) * 6.0)
        with_row = with_sample[row_frames : 2 * row_frames]
        without_row = without_sample[row_frames : 2 * row_frames]
        rms_with = sum(value * value for value in with_row) / len(with_row)
        rms_without = sum(value * value for value in without_row) / len(without_row)
        self.assertGreater(rms_with, rms_without * 3)

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

    def test_short_mod_loop_is_treated_as_one_shot(self) -> None:
        # Sample 12 in space_debris.mod has a 2-byte loop of 0x00, 0x00. A
        # Protracker-compatible player treats that as "no loop".
        data = bytes([0, 0]) + bytes([100] * 62)
        rows = empty_rows(2)
        rows[0][0] = NOTE

        with tempfile.TemporaryDirectory() as temp_dir:
            _, frames = render_frames(
                make_song(rows, sample_data=data, repeat_length=2),
                Path(temp_dir) / "one-shot.wav",
            )

        self.assertGreater(max(abs(value) for value in frames), 0.1)

    def test_sample_offset_works_with_short_loop_header(self) -> None:
        # 9xx must start playback at param * 256 bytes even when the sample's
        # tiny loop header would otherwise wrap it back to silence.
        data = bytes(256) + bytes([100] * 256)
        rows = empty_rows(2)
        rows[0][0] = Cell(period=428, sample=1, effect=0x09, param=0x01)

        with tempfile.TemporaryDirectory() as temp_dir:
            _, frames = render_frames(
                make_song(rows, sample_data=data, repeat_length=2),
                Path(temp_dir) / "offset.wav",
            )

        self.assertGreater(max(abs(value) for value in frames), 0.1)


if __name__ == "__main__":
    unittest.main()
