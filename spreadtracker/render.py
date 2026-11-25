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

# ProTracker 2.3D keeps an 8-bit phase counter, advances it by 4 * 4xx speed,
# and indexes a 32-step table with (phase >> 2) & 31. The table itself is
# unsigned; phases 128..255 make the oscillator negative. Waveforms 2 and 3 are
# both square waves in PT2. Table values follow 8bitbubsy/pt2-clone
# (pt2_tables.c), BSD-3-Clause; see THIRD_PARTY_NOTICES.md.
WAVE_PHASE_STEPS = 256
WAVE_TABLE_STEPS = 32
WAVE_TABLE: tuple[int, ...] = (
    0, 24, 49, 74, 97, 120, 141, 161, 180, 197, 212, 224, 235, 244, 250, 253,
    255, 253, 250, 244, 235, 224, 212, 197, 180, 161, 141, 120, 97, 74, 49, 24,
)

# PT2 maps each MOD note through a finetuned period table instead of applying a
# fractional multiplier. The 16 rows are finetunes 0..7 and -8..-1, as stored
# by pt2-clone (BSD-3-Clause; see THIRD_PARTY_NOTICES.md).
PERIOD_TABLE: tuple[tuple[int, ...], ...] = (
    (856, 808, 762, 720, 678, 640, 604, 570, 538, 508, 480, 453, 428, 404, 381, 360, 339, 320, 302, 285, 269, 254, 240, 226, 214, 202, 190, 180, 170, 160, 151, 143, 135, 127, 120, 113, 0),
    (850, 802, 757, 715, 674, 637, 601, 567, 535, 505, 477, 450, 425, 401, 379, 357, 337, 318, 300, 284, 268, 253, 239, 225, 213, 201, 189, 179, 169, 159, 150, 142, 134, 126, 119, 113, 0),
    (844, 796, 752, 709, 670, 632, 597, 563, 532, 502, 474, 447, 422, 398, 376, 355, 335, 316, 298, 282, 266, 251, 237, 224, 211, 199, 188, 177, 167, 158, 149, 141, 133, 125, 118, 112, 0),
    (838, 791, 746, 704, 665, 628, 592, 559, 528, 498, 470, 444, 419, 395, 373, 352, 332, 314, 296, 280, 264, 249, 235, 222, 209, 198, 187, 176, 166, 157, 148, 140, 132, 125, 118, 111, 0),
    (832, 785, 741, 699, 660, 623, 588, 555, 524, 495, 467, 441, 416, 392, 370, 350, 330, 312, 294, 278, 262, 247, 233, 220, 208, 196, 185, 175, 165, 156, 147, 139, 131, 124, 117, 110, 0),
    (826, 779, 736, 694, 655, 619, 584, 551, 520, 491, 463, 437, 413, 390, 368, 347, 328, 309, 292, 276, 260, 245, 232, 219, 206, 195, 184, 174, 164, 155, 146, 138, 130, 123, 116, 109, 0),
    (820, 774, 730, 689, 651, 614, 580, 547, 516, 487, 460, 434, 410, 387, 365, 345, 325, 307, 290, 274, 258, 244, 230, 217, 205, 193, 183, 172, 163, 154, 145, 137, 129, 122, 115, 109, 0),
    (814, 768, 725, 684, 646, 610, 575, 543, 513, 484, 457, 431, 407, 384, 363, 342, 323, 305, 288, 272, 256, 242, 228, 216, 204, 192, 181, 171, 161, 152, 144, 136, 128, 121, 114, 108, 0),
    (907, 856, 808, 762, 720, 678, 640, 604, 570, 538, 508, 480, 453, 428, 404, 381, 360, 339, 320, 302, 285, 269, 254, 240, 226, 214, 202, 190, 180, 170, 160, 151, 143, 135, 127, 120, 0),
    (900, 850, 802, 757, 715, 675, 636, 601, 567, 535, 505, 477, 450, 425, 401, 379, 357, 337, 318, 300, 284, 268, 253, 238, 225, 212, 200, 189, 179, 169, 159, 150, 142, 134, 126, 119, 0),
    (894, 844, 796, 752, 709, 670, 632, 597, 563, 532, 502, 474, 447, 422, 398, 376, 355, 335, 316, 298, 282, 266, 251, 237, 223, 211, 199, 188, 177, 167, 158, 149, 141, 133, 125, 118, 0),
    (887, 838, 791, 746, 704, 665, 628, 592, 559, 528, 498, 470, 444, 419, 395, 373, 352, 332, 314, 296, 280, 264, 249, 235, 222, 209, 198, 187, 176, 166, 157, 148, 140, 132, 125, 118, 0),
    (881, 832, 785, 741, 699, 660, 623, 588, 555, 524, 494, 467, 441, 416, 392, 370, 350, 330, 312, 294, 278, 262, 247, 233, 220, 208, 196, 185, 175, 165, 156, 147, 139, 131, 123, 117, 0),
    (875, 826, 779, 736, 694, 655, 619, 584, 551, 520, 491, 463, 437, 413, 390, 368, 347, 328, 309, 292, 276, 260, 245, 232, 219, 206, 195, 184, 174, 164, 155, 146, 138, 130, 123, 116, 0),
    (868, 820, 774, 730, 689, 651, 614, 580, 547, 516, 487, 460, 434, 410, 387, 365, 345, 325, 307, 290, 274, 258, 244, 230, 217, 205, 193, 183, 172, 163, 154, 145, 137, 129, 122, 115, 0),
    (862, 814, 768, 725, 684, 646, 610, 575, 543, 513, 484, 457, 431, 407, 384, 363, 342, 323, 305, 288, 272, 256, 242, 228, 216, 203, 192, 181, 171, 161, 152, 144, 136, 128, 121, 114, 0),
)

# Mixer options kept as constants so the Python and VBA paths stay in step.
USE_INTERPOLATION = True
RAMP_TICKS = 0.25


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


def pt_note_index(note: int) -> int:
    """Find PT2's base-table index for a MOD note period."""
    note &= 0x0FFF
    for index, value in enumerate(PERIOD_TABLE[0]):
        if note >= value:
            return index
    return 36


def pt_period(note: int, finetune: int = 0) -> float:
    """Map a MOD note through PT2's finetuned period table."""
    return float(PERIOD_TABLE[finetune & 0x0F][pt_note_index(note)])


def pt_porta_period(note: int, finetune: int = 0) -> float:
    """Map a tone-portamento target the way PT2's setTonePorta does."""
    note &= 0x0FFF
    row = PERIOD_TABLE[finetune & 0x0F]
    index = 36
    for candidate, value in enumerate(row):
        if note >= value:
            index = candidate
            break
    if index >= 37:
        index = 35
    if finetune & 8 and index > 0:
        index -= 1
    return float(row[index])


def wave_value(position: int, waveform: int = 0, sign_position: int | None = None) -> int:
    """Return the signed ProTracker 2.3D oscillator delta for a raw phase.

    ``sign_position`` mirrors PT2's tremolo ramp bug: the ramp magnitude comes
    from the tremolo phase, but which half of the ramp is used comes from the
    vibrato phase. The returned sign always follows ``position``.
    """
    phase = position % WAVE_PHASE_STEPS
    index = (phase >> 2) & (WAVE_TABLE_STEPS - 1)
    sign_phase = phase if sign_position is None else sign_position % WAVE_PHASE_STEPS
    waveform &= 0x03

    if waveform == 1:  # ramp
        magnitude = (255 - index * 8) if sign_phase >= 128 else index * 8
    elif waveform >= 2:  # square; PT2 treats type 3 like type 2
        magnitude = 255
    else:
        magnitude = WAVE_TABLE[index]

    return -magnitude if phase >= 128 else magnitude


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
    vib_active: bool = False
    vib_pos: int = 0
    vib_wave: int = 0  # E4x low nibble: waveform bits plus phase-hold bit
    trem_speed: int = 0
    trem_depth: int = 0
    trem_active: bool = False
    trem_pos: int = 0
    trem_wave: int = 0  # E7x high-nibble value: waveform bits plus hold bit
    cut_tick: int | None = None
    delay_tick: int | None = None  # EDx - retrigger this voice on this tick
    ramp_frames: int = 0


def advance_voice_tick(voice: Voice) -> None:
    """Apply the effects that run on ticks after the row trigger."""
    if voice.slide_up or voice.slide_down:
        voice.volume = max(
            0.0,
            min(1.0, voice.volume + (voice.slide_up - voice.slide_down) / 64.0),
        )
    # ProTracker clamps 1xx/2xx to the lowest/highest period in its table.
    if voice.port_up:
        voice.period = max(113.0, voice.period - voice.port_up)
    if voice.port_down:
        voice.period = min(856.0, voice.period + voice.port_down)
    if voice.port_speed and voice.target_period:
        if voice.period < voice.target_period:
            voice.period = min(voice.target_period, voice.period + voice.port_speed)
        elif voice.period > voice.target_period:
            voice.period = max(voice.target_period, voice.period - voice.port_speed)

    # A row without 4xx/6xx/7xx clears that channel's command, so the phase
    # only advances on rows carrying an active vibrato/tremolo command.
    if voice.vib_active:
        voice.vib_pos = (voice.vib_pos + 4 * voice.vib_speed) % WAVE_PHASE_STEPS
    if voice.trem_active:
        voice.trem_pos = (voice.trem_pos + 4 * voice.trem_speed) % WAVE_PHASE_STEPS


def effective_period(voice: Voice, tick: int) -> float:
    """Return the playback period after arpeggio and vibrato."""
    period = voice.period
    if voice.arp_up or voice.arp_down:
        semitones = (0, voice.arp_up, voice.arp_down)[tick % 3]
        if semitones:
            period *= 2.0 ** (-semitones / 12.0)

    # ProTracker 2.3D does not apply vibrato on the first tick of a row.
    # Afterwards its unsigned table delta is scaled by depth / 128 and added to
    # or subtracted from the period according to the phase's high bit. The
    # stored phase has already advanced for this tick, so read the previous
    # phase like the original replay routine.
    if voice.vib_active and voice.vib_depth and tick > 0:
        phase = (voice.vib_pos - 4 * voice.vib_speed) % WAVE_PHASE_STEPS
        period += int(wave_value(phase, voice.vib_wave) * voice.vib_depth / 128)

    return max(1.0, period)


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
    pending_tempo: float | None = None
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
        nonlocal pending_jump, pending_break, pending_tempo, pattern_delay_rows

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
            # EDx belongs to this row only; a delay that was not reached before
            # the row ended must not leak into the next one.
            voice.delay_tick = None

            # OpenMPT clears the vibrato/tremolo flags at every row boundary;
            # only a 4xx/6xx/7xx command on this row turns the oscillator back
            # on. Speed/depth remain in effect memory, but the LFO must not
            # keep modulating (or advancing) on rows without a command.
            voice.vib_active = effect in (0x04, 0x06)
            voice.trem_active = effect == 0x07

            if cell.sample > 0:
                instrument = song.instruments.get(cell.sample)
                sample_data = song.sample_data.get(cell.sample)
                if instrument is None or not sample_data:
                    voice.active = False
                    continue

                # PT2 loads the sample pointer, volume, loop and finetune from
                # the cell even when there is no note. An idle channel stays
                # idle, but the selected sample is ready for a later note with
                # no sample number.
                voice.data = sample_data
                voice.length = len(sample_data)
                # MOD sample-header volume is literal: 0 is silent, 64 full.
                voice.volume = min(1.0, instrument.volume / 64.0)
                # MOD repeat length is in bytes. Lengths of 2 bytes or less are
                # "no loop" in Protracker-compatible players, so honor the
                # whole sample instead of a silent 2-byte loop.
                voice.looping = instrument.repeat_length > 2
                voice.loop_start = instrument.repeat_start
                voice.loop_end = (
                    min(instrument.repeat_start + instrument.repeat_length, len(sample_data))
                    if voice.looping
                    else len(sample_data)
                )
                voice.fine_tune = instrument.finetune & 0x0F

                if cell.period > 0:
                    note_finetune = voice.fine_tune
                    if effect == 0x0E and up == 0x05:
                        note_finetune = down & 0x0F
                    if effect in (0x03, 0x05):
                        # Tone portamento sets a target without retriggering
                        # DMA, resetting LFO phases, or starting an idle voice.
                        voice.target_period = pt_porta_period(cell.period, note_finetune)
                    else:
                        voice.period = pt_period(cell.period, note_finetune)
                        voice.target_period = voice.period
                        if not voice.vib_wave & 0x04:
                            voice.vib_pos = 0
                        if not voice.trem_wave & 0x04:
                            voice.trem_pos = 0
                        voice.cut_tick = None
                        if effect == 0x0E and up == 0x0D and down > 0:
                            # EDx loads the sample and period at tick 0 but
                            # holds the DMA retrig back until tick x.
                            voice.delay_tick = down
                        else:
                            voice.active = True
                            voice.position = 0.0
                            voice.ramp_frames = int(RAMP_TICKS * frames_per_tick)
            elif cell.period > 0 and voice.data is not None:
                # A note without a sample number still retriggers the selected
                # sample in PT2, except for tone portamento which only retargets
                # the slide.
                note_finetune = voice.fine_tune
                if effect == 0x0E and up == 0x05:
                    note_finetune = down & 0x0F
                if effect in (0x03, 0x05):
                    voice.target_period = pt_porta_period(cell.period, note_finetune)
                else:
                    voice.period = pt_period(cell.period, note_finetune)
                    voice.target_period = voice.period
                    if not voice.vib_wave & 0x04:
                        voice.vib_pos = 0
                    if not voice.trem_wave & 0x04:
                        voice.trem_pos = 0
                    voice.cut_tick = None
                    if effect == 0x0E and up == 0x0D and down > 0:
                        voice.delay_tick = down
                    else:
                        voice.active = True
                        voice.position = 0.0
                        voice.ramp_frames = int(RAMP_TICKS * frames_per_tick)

            # Row-trigger effects. Zero nibbles mean "hold" for vibrato/tremolo
            # parameters, but clear arpeggio and slides in classic Protracker.
            voice.arp_up, voice.arp_down = ((up, down) if effect == 0x00 else (0, 0))
            voice.port_up = param if effect == 0x01 else 0
            voice.port_down = param if effect == 0x02 else 0
            if effect == 0x03 and param:
                voice.port_speed = param
            if effect == 0x04:
                # 6xy only volume-slides; its nibbles are not vibrato settings.
                # Protracker keeps the last 4xy speed/depth as vibrato memory.
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
                # MOD/XM volume slides use nibble priority: a non-zero high
                # nibble means slide up and the low nibble is ignored.
                if up:
                    voice.slide_up, voice.slide_down = up, 0
                else:
                    voice.slide_up, voice.slide_down = 0, down
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
                # ProTracker interprets the byte as BCD and maps values above
                # the last row back to row 0, rather than clamping to row 63.
                bcd_break = up * 10 + down
                pending_break = bcd_break if bcd_break < PATTERN_ROWS else 0
            elif effect == 0x0F and param:  # Fxx - speed / tempo
                if param <= 0x1F:
                    ticks_per_row = param
                else:
                    # PT's CIA tempo change is delayed until the next tick.
                    pending_tempo = float(param)

            if effect == 0x0E and up == 0x0E:  # EEx - pattern delay is global
                pattern_delay_rows = down

            if not voice.active:
                continue

            if effect == 0x0E:  # Exy - extended commands
                if up == 0x01:  # E1x - fine portamento up
                    voice.period = max(113.0, voice.period - down)
                elif up == 0x02:  # E2x - fine portamento down
                    voice.period = min(856.0, voice.period + down)
                elif up == 0x04:  # E4x - vibrato waveform/phase control
                    voice.vib_wave = down
                elif up == 0x05:  # E5x - set finetune
                    voice.fine_tune = down & 0x0F
                elif up == 0x07:  # E7x - tremolo waveform/phase control
                    voice.trem_wave = down
                elif up == 0x09:  # E9x - retrigger sample
                    # PT2's doRetrg restarts the sample without resetting LFOs.
                    voice.position = 0.0
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
                    if voice.delay_tick is not None and tick == voice.delay_tick:
                        # PT2's noteDelay calls doRetrg without touching LFO
                        # state, so restart only the sample pointer here.
                        voice.active = True
                        voice.position = 0.0
                        voice.cut_tick = None
                        voice.ramp_frames = int(RAMP_TICKS * frames_per_tick)
                        voice.delay_tick = None
                    elif voice.active:
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

            # PT2 skips tremolo on tick 0. Its table delta is scaled by depth
            # / 64 in 0..64 volume units, then normalized to 0..1 here. PT2's
            # ramp waveform also takes its sign from the vibrato phase.
            if voice.trem_active and voice.trem_depth and tick > 0:
                phase = (voice.trem_pos - 4 * voice.trem_speed) % WAVE_PHASE_STEPS
                trem = (
                    wave_value(phase, voice.trem_wave, sign_position=voice.vib_pos)
                    * voice.trem_depth
                    / (64.0 * 64.0)
                )
                level = max(0.0, min(1.0, level + trem))

            accumulator += _voice_sample(voice) * level
            voice.position += AMIGA_CLOCK / (period * 2.0 * sample_rate)

        mix.append(accumulator / CHANNEL_COUNT)

        tick_phase += 1.0
        while tick_phase >= frames_per_tick and order_pos < orders:
            tick_phase -= frames_per_tick
            if pending_tempo is not None:
                tempo = pending_tempo
                frames_per_tick = sample_rate * 5.0 / (2.0 * tempo)
                pending_tempo = None
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
