"""Optional cross-platform playback helpers for rendered WAV files.

Rendering never depends on this module. When available, playback uses
``sounddevice`` or ``pygame``; otherwise it falls back to a platform audio
player launched in the background.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import wave
from pathlib import Path

_external_process: subprocess.Popen[bytes] | None = None


def _read_wav_info(path: Path) -> tuple[int, int, int] | None:
    try:
        with wave.open(str(path), "rb") as wav:
            return wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
    except (OSError, EOFError, wave.Error):
        return None


def _play_with_sounddevice(path: Path) -> bool:
    try:
        import numpy as np
        import sounddevice as sd
    except ImportError:
        return False

    info = _read_wav_info(path)
    if info is None:
        return False
    rate, channels, sample_width = info
    if sample_width != 2:
        return False

    with wave.open(str(path), "rb") as wav:
        raw = wav.readframes(wav.getnframes())
    samples = np.frombuffer(raw, dtype="<i2")
    if channels > 1:
        samples = samples.reshape(-1, channels)
    sd.play(samples, samplerate=rate)
    return True


def _play_with_pygame(path: Path) -> bool:
    try:
        import pygame
    except ImportError:
        return False

    info = _read_wav_info(path)
    if info is None:
        return False
    rate, channels, _ = info

    try:
        if not pygame.get_init():
            pygame.init()
        if pygame.mixer.get_init() is None:
            pygame.mixer.init(frequency=rate, channels=channels)
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
    except Exception:
        return False
    return True


def _external_player_command(path: Path) -> list[str] | None:
    text = str(path)
    if sys.platform == "win32":
        escaped = text.replace("'", "''")
        return [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(New-Object Media.SoundPlayer '{escaped}').PlaySync()",
        ]
    if sys.platform == "darwin" and shutil.which("afplay"):
        return ["afplay", text]
    if shutil.which("aplay"):
        return ["aplay", "-q", text]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", text]
    return None


def _play_with_external_player(path: Path) -> bool:
    global _external_process
    command = _external_player_command(path)
    if command is None:
        return False
    try:
        _external_process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return True


def play_wav(path: Path | str) -> bool:
    """Start asynchronous playback of ``path``. Returns False if unavailable."""
    global _external_process
    target = Path(path)
    if not target.is_file():
        return False

    stop_playback()
    if _play_with_sounddevice(target):
        return True
    if _play_with_pygame(target):
        return True
    if _play_with_external_player(target):
        return True
    return False


def stop_playback() -> None:
    """Stop playback started by :func:`play_wav`, if possible."""
    global _external_process

    try:
        import sounddevice as sd

        sd.stop()
    except Exception:
        pass

    try:
        import pygame

        if pygame.mixer.get_init() is not None:
            pygame.mixer.music.stop()
    except Exception:
        pass

    if _external_process is not None:
        if _external_process.poll() is None:
            _external_process.terminate()
        _external_process = None
