"""Frontend-independent WAV renderer for Spreadtracker."""

from __future__ import annotations

import sys
import wave
from array import array
from pathlib import Path

from .model import (
    AMIGA_CLOCK,
    CHANNEL_COUNT,
    PATTERN_ROWS,
    Cell,
    Song,
)


def _positive_int(value: object, default: int, minimum: int = 1) -> int:
    try:
        result = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if result >= minimum else default


def _positive_float(value: object, default: float) -> float:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _signed_sample(value: int) -> float:
    """Decode a classic MOD byte as signed 8-bit PCM."""
    return (value if value < 128 else value - 256) / 128.0


def _nibbles(param: int) -> tuple[int, int]:
    return (param >> 4) & 0x0F, param & 0x0F


def render_song(song: Song, out_path: Path | str | None = None) -> Path:
    """Render ``song`` to a 16-bit mono WAV and return the output path.

    The renderer mirrors the VBA prototype: note triggers, instrument volume,
    ``0xx`` arpeggio, ``9xx`` sample offsets, ``Axx`` volume slide, ``Cxx``
    set-volume, sample loops, static tempo/speed, and nearest-neighbour
    resampling. Other effects are parsed, not rendered.
    """
    settings = song.settings
    tempo = _positive_float(settings.tempo, 125.0)
    speed = _positive_float(settings.speed, 6.0)
    ticks_per_row = max(1, int(round(speed)))
    sample_rate = _positive_int(settings.sample_rate, 22050, 8000)
    requested_orders = _positive_int(settings.orders, 1, 1)

    order = list(song.order)
    if not order:
        raise ValueError("Cannot render a song with an empty order list")
    orders = min(requested_orders, len(order))

    # Protracker timing: ticks/sec = BPM * 2 / 5; one row per Speed ticks.
    row_seconds = speed * 5.0 / (tempo * 2.0)
    total_rows = orders * PATTERN_ROWS
    total_frames = max(1, int(total_rows * row_seconds * sample_rate))
    frames_per_row = max(1, int(row_seconds * sample_rate))

    mix = array("f", [0.0]) * total_frames

    active = [False] * CHANNEL_COUNT
    data_for_voice: list[bytes | None] = [None] * CHANNEL_COUNT
    position = [0.0] * CHANNEL_COUNT
    base_period = [0.0] * CHANNEL_COUNT
    volume = [0.0] * CHANNEL_COUNT
    looping = [False] * CHANNEL_COUNT
    loop_start = [0] * CHANNEL_COUNT
    loop_end = [0] * CHANNEL_COUNT
    arpeggio = [(0, 0)] * CHANNEL_COUNT
    volume_slide = [(0, 0)] * CHANNEL_COUNT

    cached_pattern = -1
    current_pattern: list[list[Cell]] | None = None

    for frame in range(total_frames):
        within_row = frame % frames_per_row
        row_index = frame // frames_per_row
        tick = min(ticks_per_row - 1, within_row * ticks_per_row // frames_per_row)

        if within_row == 0 and row_index < total_rows:
            pattern_index = order[row_index // PATTERN_ROWS]
            if pattern_index != cached_pattern:
                if 0 <= pattern_index < len(song.patterns):
                    current_pattern = song.patterns[pattern_index]
                else:
                    current_pattern = None
                cached_pattern = pattern_index

            if current_pattern is not None:
                row = row_index % PATTERN_ROWS
                if 0 <= row < len(current_pattern):
                    for channel in range(CHANNEL_COUNT):
                        cell = current_pattern[row][channel]

                        if cell.period and cell.sample:
                            instrument = song.instruments.get(cell.sample)
                            sample_data = song.sample_data.get(cell.sample)
                            if instrument is None or not sample_data:
                                active[channel] = False
                                continue

                            data_for_voice[channel] = sample_data
                            active[channel] = True
                            base_period[channel] = float(cell.period)

                            looping[channel] = instrument.repeat_length > 1
                            loop_start[channel] = instrument.repeat_start
                            if looping[channel]:
                                loop_end[channel] = min(
                                    instrument.repeat_start + instrument.repeat_length,
                                    len(sample_data),
                                )
                            else:
                                loop_end[channel] = len(sample_data)

                            volume[channel] = min(1.0, instrument.volume / 64.0)
                            if cell.effect == 0x0C:  # Cxx - set volume
                                volume[channel] = min(1.0, cell.param / 64.0)

                            position[channel] = 0.0
                            if cell.effect == 0x09:  # 9xx - sample offset
                                position[channel] = float(cell.param * 256)
                            if position[channel] >= len(sample_data):
                                active[channel] = False
                        elif active[channel]:
                            # Effects on rows without a new note modify the
                            # current voice in Protracker.
                            if cell.effect == 0x09:  # 9xx - sample offset
                                position[channel] = float(cell.param * 256)
                            elif cell.effect == 0x0C:  # Cxx - set volume
                                volume[channel] = min(1.0, cell.param / 64.0)

                        if active[channel]:
                            arpeggio[channel] = (
                                _nibbles(cell.param) if cell.effect == 0x00 else (0, 0)
                            )
                            volume_slide[channel] = (
                                _nibbles(cell.param) if cell.effect == 0x0A else (0, 0)
                            )

        accumulator = 0.0
        for channel in range(CHANNEL_COUNT):
            if not active[channel]:
                continue

            data = data_for_voice[channel]
            if not data:
                active[channel] = False
                continue

            up, down = volume_slide[channel]
            if tick > 0 and (up or down):
                volume[channel] = max(
                    0.0, min(1.0, volume[channel] + (up - down) / 64.0)
                )

            period = base_period[channel]
            arp_up, arp_down = arpeggio[channel]
            if (arp_up or arp_down) and period > 0:
                delta = (0, arp_up, arp_down)[tick % 3]
                period *= 2.0 ** (-delta / 12.0)
            if period <= 0:
                active[channel] = False
                continue

            sample_index = int(position[channel])
            upper = len(data)

            if sample_index >= loop_end[channel]:
                if looping[channel] and loop_end[channel] > loop_start[channel]:
                    position[channel] = float(loop_start[channel])
                    sample_index = loop_start[channel]
                else:
                    active[channel] = False

            if active[channel] and sample_index >= upper:
                if looping[channel] and loop_end[channel] > loop_start[channel]:
                    position[channel] = float(loop_start[channel])
                    sample_index = loop_start[channel]
                else:
                    active[channel] = False

            if not active[channel]:
                continue

            if 0 <= sample_index < upper:
                accumulator += _signed_sample(data[sample_index]) * volume[channel]

            position[channel] += AMIGA_CLOCK / (period * 2.0 * sample_rate)

        mix[frame] = accumulator / CHANNEL_COUNT

    pcm = array("h")
    for value in mix:
        clipped = max(-1.0, min(1.0, float(value)))
        pcm.append(int(clipped * 32767.0))
    if sys.byteorder == "big":
        pcm.byteswap()

    target = Path(out_path) if out_path is not None else Path(settings.wav_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())

    return target
