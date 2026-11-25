"""Frontend-independent WAV renderer for Spreadtracker."""

from __future__ import annotations

import sys
import wave
from array import array
from dataclasses import dataclass
from pathlib import Path

from .model import (
    AMIGA_CLOCK,
    CHANNEL_COUNT,
    PATTERN_ROWS,
    Cell,
    Song,
)

# Protracker's 32-step sine table, centred on 128. Waveforms 1-3 are generated
# from the same phase counter for E4x/E7x waveform selection.
WAVE_TABLE: tuple[int, ...] = (
    0, 24, 49, 74, 97, 120, 141, 161, 180, 197, 212, 225, 236, 244, 250, 253,
    255, 253, 250, 244, 236, 225, 212, 197, 180, 161, 141, 120, 97, 74, 49, 24,
)

# Mixer options kept as constants so the Python and VBA paths stay in step.
USE_INTERPOLATION = True
RAMP_TICKS = 0.25
VIBRATO_UNITS_PER_SEMITONE = 8.0


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


def wave_value(position: int, waveform: int = 0) -> int:
    """Return a Protracker LFO value in the range -128..127."""
    position %= len(WAVE_TABLE)
    if waveform == 1:  # ramp down / saw down
        return 127 - position * 8
    if waveform == 2:  # square
        return -128 if position < 16 else 127
    if waveform == 3:  # ramp up / saw up
        return position * 8 - 128
    return WAVE_TABLE[position] - 128


@dataclass
class Voice:
    """Mutable per-channel playback state."""

    active: bool = False
    data: bytes | None = None
    length: int = 0
    position: float = 0.0
    period: float = 0.0
    target_period: float = 0.0
    volume: float = 0.0
    looping: bool = False
    loop_start: int = 0
    loop_end: int = 0
    fine_tune: int = 0
    arp_up: int = 0
    arp_down: int = 0
    slide_up: int = 0
    slide_down: int = 0
    port_up: int = 0
    port_down: int = 0
    port_speed: int = 0
    vib_speed: int = 0
    vib_depth: int = 0
    vib_pos: int = 0
    vib_wave: int = 0
    trem_speed: int = 0
    trem_depth: int = 0
    trem_pos: int = 0
    trem_wave: int = 0
    cut_tick: int | None = None
    ramp_frames: int = 0


def advance_voice_tick(voice: Voice) -> None:
    """Apply the effects that run on ticks after the row trigger."""
    if voice.slide_up or voice.slide_down:
        voice.volume = max(
            0.0,
            min(1.0, voice.volume + (voice.slide_up - voice.slide_down) / 64.0),
        )
    if voice.port_up:
        voice.period = max(1.0, voice.period - voice.port_up)
    if voice.port_down:
        voice.period += voice.port_down
    if voice.port_speed and voice.target_period:
        if voice.period < voice.target_period:
            voice.period = min(voice.target_period, voice.period + voice.port_speed)
        elif voice.period > voice.target_period:
            voice.period = max(voice.target_period, voice.period - voice.port_speed)

    voice.vib_pos = (voice.vib_pos + voice.vib_speed) % len(WAVE_TABLE)
    voice.trem_pos = (voice.trem_pos + voice.trem_speed) % len(WAVE_TABLE)


def effective_period(voice: Voice, tick: int) -> float:
    """Return the playback period after arpeggio, vibrato and finetune."""
    period = voice.period
    if voice.arp_up or voice.arp_down:
        semitones = (0, voice.arp_up, voice.arp_down)[tick % 3]
        if semitones:
            period *= 2.0 ** (-semitones / 12.0)

    if voice.vib_depth:
        offset = wave_value(voice.vib_pos, voice.vib_wave) * voice.vib_depth / 128.0
        if offset:
            period *= 2.0 ** (-offset / (12.0 * VIBRATO_UNITS_PER_SEMITONE))

    if voice.fine_tune:
        fine = voice.fine_tune if voice.fine_tune < 8 else voice.fine_tune - 16
        period *= 2.0 ** (-fine / (12.0 * 8.0))

    return period


def _sample_value(data: bytes, index: int, voice: Voice) -> float:
    if voice.looping and voice.loop_end > voice.loop_start and index >= voice.loop_end:
        size = voice.loop_end - voice.loop_start
        index = voice.loop_start + ((index - voice.loop_start) % size)
    else:
        index = max(0, min(index, len(data) - 1))
    return _signed_sample(data[index]) if 0 <= index < len(data) else 0.0


def _voice_sample(voice: Voice) -> float:
    data = voice.data
    if not data:
        voice.active = False
        return 0.0

    index = int(voice.position)
    if not voice.looping and voice.position >= len(data):
        voice.active = False
        return 0.0

    if not USE_INTERPOLATION:
        return _sample_value(data, index, voice)

    fraction = voice.position - index
    low = _sample_value(data, index, voice)
    high = _sample_value(data, index + 1, voice)
    return low * (1.0 - fraction) + high * fraction


def render_song(song: Song, out_path: Path | str | None = None) -> Path:
    """Render ``song`` to a 16-bit mono WAV and return the output path.

    The sequencer is tick-driven so ``Fxx`` speed/tempo changes take effect in
    the row where they occur. Continuous effects are held in per-channel state
    and run on every tick after the row trigger.
    """
    settings = song.settings
    tempo = _positive_float(settings.tempo, 125.0)
    ticks_per_row = max(1, int(round(_positive_float(settings.speed, 6.0))))
    sample_rate = _positive_int(settings.sample_rate, 22050, 8000)
    requested_orders = _positive_int(settings.orders, 1, 1)
    frames_per_tick = sample_rate * 5.0 / (2.0 * tempo)

    order = list(song.order)
    if not order:
        raise ValueError("Cannot render a song with an empty order list")
    orders = min(requested_orders, len(order))

    voices = [Voice() for _ in range(CHANNEL_COUNT)]
    mix = array("f")

    order_pos = 0
    row = 0
    tick = 0
    tick_phase = 0.0
    effects_tick = -1
    cached_pattern = -1
    current_pattern: list[list[Cell]] | None = None
    pending_jump: int | None = None
    pending_break: int | None = None
    pattern_delay_rows = 0

    # Upper bound for dynamic speed changes; rendering stops as soon as the
    # requested orders have been played. Slower BPM than the starting value is
    # possible after F20-FF, hence the 32 BPM floor.
    worst_bpm = min(tempo, 32.0)
    max_frames = int(
        orders
        * PATTERN_ROWS
        * max(31, ticks_per_row)
        * 5.0
        / (2.0 * worst_bpm)
        * sample_rate
    ) + sample_rate

    def load_pattern(index: int) -> None:
        nonlocal cached_pattern, current_pattern
        if index != cached_pattern:
            current_pattern = song.patterns[index] if 0 <= index < len(song.patterns) else None
            cached_pattern = index

    def process_row() -> bool:
        nonlocal order_pos, row, ticks_per_row, tempo, frames_per_tick
        nonlocal pending_jump, pending_break, pattern_delay_rows

        if order_pos >= orders:
            return False

        if pending_break is not None:
            order_pos += 1
            row = max(0, min(pending_break, PATTERN_ROWS - 1))
            pending_break = None

        if order_pos >= orders:
            return False

        load_pattern(order[order_pos])
        if current_pattern is None or row >= len(current_pattern):
            return True

        for channel, cell in enumerate(current_pattern[row]):
            voice = voices[channel]
            effect = cell.effect
            param = cell.param
            up, down = _nibbles(param)

            if cell.sample > 0:
                instrument = song.instruments.get(cell.sample)
                sample_data = song.sample_data.get(cell.sample)
                if instrument is None or not sample_data:
                    voice.active = False
                    continue

                has_note = cell.period > 0
                if not has_note and not voice.active:
                    # A sample number without a note selects the sample only;
                    # it does not start a silent channel.
                    pass
                else:
                    previous_period = voice.period
                    voice.active = True
                    voice.data = sample_data
                    voice.length = len(sample_data)
                    if has_note:
                        voice.position = 0.0
                        if effect in (0x03, 0x05) and previous_period > 0:
                            # Tone portamento starts from the previous note and
                            # slides toward the new pitch.
                            voice.period = previous_period
                            voice.target_period = float(cell.period)
                        else:
                            voice.period = float(cell.period)
                            voice.target_period = voice.period
                        voice.vib_pos = 0
                        voice.trem_pos = 0
                        voice.cut_tick = None
                        voice.ramp_frames = int(RAMP_TICKS * frames_per_tick)
                    # A sample number without a note keeps the current period
                    # and sample position, but still reloads the sample header
                    # (most importantly the sample volume) like Protracker.
                    # MOD sample-header volume is literal: 0 is silent, 64 full.
                    voice.volume = min(1.0, instrument.volume / 64.0)
                    # MOD repeat length is in bytes. Lengths of 2 bytes or less
                    # are "no loop" in Protracker-compatible players, so honor
                    # the whole sample instead of a silent 2-byte loop.
                    voice.looping = instrument.repeat_length > 2
                    voice.loop_start = instrument.repeat_start
                    voice.loop_end = (
                        min(instrument.repeat_start + instrument.repeat_length, len(sample_data))
                        if voice.looping
                        else len(sample_data)
                    )
                    voice.fine_tune = instrument.finetune & 0x0F
            elif cell.period > 0 and cell.sample == 0 and voice.active:
                if effect in (0x03, 0x05):
                    voice.target_period = float(cell.period)
                else:
                    voice.period = float(cell.period)
                    voice.target_period = voice.period
                voice.vib_pos = 0
                voice.trem_pos = 0

            # Row-trigger effects. Zero nibbles mean "hold" for vibrato/tremolo
            # parameters, but clear arpeggio and slides in classic Protracker.
            voice.arp_up, voice.arp_down = ((up, down) if effect == 0x00 else (0, 0))
            voice.port_up = param if effect == 0x01 else 0
            voice.port_down = param if effect == 0x02 else 0
            if effect == 0x03 and param:
                voice.port_speed = param
            if effect in (0x04, 0x06):
                if up:
                    voice.vib_speed = up
                if down:
                    voice.vib_depth = down
            if effect == 0x07:
                if up:
                    voice.trem_speed = up
                if down:
                    voice.trem_depth = down
            if effect in (0x0A, 0x05, 0x06):
                voice.slide_up, voice.slide_down = up, down
            else:
                voice.slide_up, voice.slide_down = 0, 0
            if effect == 0x09:
                voice.position = float(param * 256)
            elif effect == 0x0C:
                voice.volume = min(1.0, param / 64.0)

            # Global effects must run even when this channel is silent.
            if effect == 0x0B:  # Bxx - jump at the end of this pattern
                pending_jump = min(param, len(order) - 1)
            elif effect == 0x0D and pending_jump is None:  # Dxx - pattern break
                pending_break = min(param, PATTERN_ROWS - 1)
            elif effect == 0x0F and param:  # Fxx - speed / tempo
                if param <= 0x1F:
                    ticks_per_row = param
                else:
                    tempo = float(param)
                    frames_per_tick = sample_rate * 5.0 / (2.0 * tempo)

            if effect == 0x0E and up == 0x0E:  # EEx - pattern delay is global
                pattern_delay_rows = down

            if not voice.active:
                continue

            if effect == 0x0E:  # Exy - extended commands
                if up == 0x01:  # E1x - fine portamento up
                    voice.period = max(1.0, voice.period - down)
                elif up == 0x02:  # E2x - fine portamento down
                    voice.period += down
                elif up == 0x04:  # E4x - vibrato waveform
                    voice.vib_wave = down & 0x03
                elif up == 0x05:  # E5x - set finetune
                    voice.fine_tune = down & 0x0F
                elif up == 0x07:  # E7x - tremolo waveform
                    voice.trem_wave = down & 0x03
                elif up == 0x09:  # E9x - retrigger sample
                    voice.position = 0.0
                    voice.vib_pos = 0
                    voice.trem_pos = 0
                elif up == 0x0A:  # EAx - fine volume slide up
                    voice.volume = min(1.0, voice.volume + 1.0 / 64.0)
                elif up == 0x0B:  # EBx - fine volume slide down
                    voice.volume = max(0.0, voice.volume - 1.0 / 64.0)
                elif up == 0x0C:  # ECx - note cut after x ticks
                    voice.cut_tick = down

        return True

    def advance_row() -> None:
        nonlocal row, order_pos, tick, pattern_delay_rows, pending_jump
        if pattern_delay_rows > 0:
            pattern_delay_rows -= 1
            tick = 0
            return

        if pending_jump is not None:
            if row < PATTERN_ROWS - 1:
                row += 1
            else:
                if order_pos + 1 >= orders:
                    # A Bxx on the final requested order ends the song rather
                    # than looping back for another pass until the frame cap.
                    order_pos = orders
                else:
                    order_pos = max(0, min(pending_jump, orders - 1))
                row = 0
                pending_jump = None
        elif pending_break is None:
            # A pending Dxx is consumed by process_row at the start of the next
            # row; Bxx and ordinary row advances move the row pointer here.
            row += 1
            if row >= PATTERN_ROWS:
                row = 0
                order_pos += 1

        tick = 0
        if order_pos < orders:
            process_row()

    process_row()

    while order_pos < orders and len(mix) < max_frames:
        # Continuous effects run once per tick, not once per output frame.
        # A tick spans frames_per_tick frames, so track the last tick we
        # processed to avoid applying slides/LFOs repeatedly within one tick.
        if tick != effects_tick:
            effects_tick = tick
            if tick > 0:
                for voice in voices:
                    if voice.active:
                        advance_voice_tick(voice)

        accumulator = 0.0

        for voice in voices:
            if not voice.active:
                continue

            if voice.cut_tick is not None and tick >= voice.cut_tick:
                voice.volume = 0.0

            period = effective_period(voice, tick)
            if period <= 0:
                voice.active = False
                continue

            level = voice.volume
            if voice.ramp_frames > 0:
                ramp_length = max(1.0, RAMP_TICKS * frames_per_tick)
                level *= min(1.0, voice.ramp_frames / ramp_length)
                voice.ramp_frames -= 1

            if voice.trem_depth:
                trem = (
                    wave_value(voice.trem_pos, voice.trem_wave)
                    * voice.trem_depth
                    / 64.0
                )
                level = max(0.0, min(1.0, level + trem))

            accumulator += _voice_sample(voice) * level
            voice.position += AMIGA_CLOCK / (period * 2.0 * sample_rate)

        mix.append(accumulator / CHANNEL_COUNT)

        tick_phase += 1.0
        while tick_phase >= frames_per_tick and order_pos < orders:
            tick_phase -= frames_per_tick
            tick += 1
            if tick >= ticks_per_row:
                advance_row()

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
