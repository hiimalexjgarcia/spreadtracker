"""LibreOffice Calc Python macro entry points for Spreadtracker.

Install this file together with the ``spreadtracker`` package in your
LibreOffice Python scripts directory. See README.md for details.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

# LibreOffice's script browser expects bare function names here, so this is
# assigned at the bottom of the file after the functions are defined.
_core = None


def _local_path(value: str) -> Path:
    """Accept either a filesystem path or a LibreOffice file:// URL."""
    if value.startswith("file:"):
        path = unquote(urlparse(value).path)
        if os.name == "nt" and path.startswith("/"):
            path = path[1:]
        return Path(path)
    return Path(value)


def _ensure_import_path() -> None:
    """Add the macro's own directory to sys.path when first called.

    LibreOffice executes Python macro modules without ``__file__`` set during
    module import, but sets it to a file:// URL afterwards. Deferring this
    bootstrap until a macro is invoked keeps both behaviours working.
    """
    candidates: list[Path] = []
    script_file = globals().get("__file__")
    if script_file:
        candidates.append(_local_path(str(script_file)).parent)
    if os.environ.get("SPREADTRACKER_ROOT"):
        candidates.append(Path(os.environ["SPREADTRACKER_ROOT"]))

    for candidate in candidates:
        if str(candidate) not in sys.path:
            sys.path.insert(0, str(candidate))


def _core_module():
    global _core
    if _core is None:
        _ensure_import_path()
        from spreadtracker import calc as core  # noqa: PLC0415

        _core = core
    return _core


def _document():
    return XSCRIPTCONTEXT.getDocument()  # noqa: F821  (injected by LibreOffice)


def RenderAndPlay(document=None) -> str:
    """Render the open workbook and start playback."""
    return _core_module().render_and_play(document if document is not None else _document())


def RenderWav(document=None) -> str:
    """Render the open workbook to WAV without playing it."""
    return _core_module().render_wav(document if document is not None else _document())


def StopPlayback() -> None:
    """Stop the last playback started by RenderAndPlay."""
    _core_module().stop_playback()


# Must use bare names, not strings, for LibreOffice's macro browser.
g_exportedScripts = RenderAndPlay, RenderWav, StopPlayback
