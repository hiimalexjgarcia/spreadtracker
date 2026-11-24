"""LibreOffice Calc Python macro entry points for Spreadtracker.

Install this file together with the ``spreadtracker`` package in your
LibreOffice Python scripts directory. See README.md for details.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# LibreOffice does not add macro directories to sys.path. Both this file and
# the spreadtracker package are expected in the same Scripts/python folder.
_candidates = [Path(__file__).resolve().parent]
if os.environ.get("SPREADTRACKER_ROOT"):
    _candidates.append(Path(os.environ["SPREADTRACKER_ROOT"]))
for _candidate in _candidates:
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from spreadtracker import calc as core  # noqa: E402

g_exportedScripts = ("RenderAndPlay", "RenderWav", "StopPlayback")


def _document():
    return XSCRIPTCONTEXT.getDocument()  # noqa: F821  (injected by LibreOffice)


def RenderAndPlay() -> str:
    """Render the open workbook and start playback."""
    return core.render_and_play(_document())


def RenderWav() -> str:
    """Render the open workbook to WAV without playing it."""
    return core.render_wav(_document())


def StopPlayback() -> None:
    """Stop the last playback started by RenderAndPlay."""
    core.stop_playback()
