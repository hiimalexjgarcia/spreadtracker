#!/usr/bin/env python3
"""Live LibreOffice Calc check for the Spreadtracker Python macro.

Starts a private headless LibreOffice instance, installs the Calc macro and
shared package into a temporary user profile, opens the generated workbook,
invokes RenderWav through the UNO script provider, and verifies the WAV.

This needs LibreOffice with Python script provider support and the `uno` module
available to the Python interpreter used to run this script.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import tempfile
import time
import wave
from pathlib import Path

import uno
from com.sun.star.beans import PropertyValue

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKBOOK = ROOT / "workbook" / "spreadtracker.xlsx"
MACRO_SOURCE = ROOT / "calc" / "Spreadtracker.py"
PACKAGE_SOURCE = ROOT / "spreadtracker"
SCRIPT_URL = (
    "vnd.sun.star.script:Spreadtracker.py$RenderWav"
    "?language=Python&location=user"
)
PLAY_SCRIPT_URL = (
    "vnd.sun.star.script:Spreadtracker.py$RenderAndPlay"
    "?language=Python&location=user"
)
STOP_SCRIPT_URL = (
    "vnd.sun.star.script:Spreadtracker.py$StopPlayback"
    "?language=Python&location=user"
)


def property_value(name: str, value: object) -> PropertyValue:
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def connect_to_libreoffice(port: int, timeout: float = 45.0):
    local_context = uno.getComponentContext()
    resolver = local_context.ServiceManager.createInstanceWithContext(
        "com.sun.star.bridge.UnoUrlResolver", local_context
    )
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return resolver.resolve(
                f"uno:socket,host=127.0.0.1,port={port};urp;"
                "StarOffice.ComponentContext"
            )
        except Exception as exc:  # LibreOffice is still starting.
            last_error = exc
            time.sleep(0.25)
    raise RuntimeError(f"Could not connect to LibreOffice: {last_error}")


def set_song_cells(doc, wav_path: Path) -> None:
    sheet = doc.Sheets.getByName("Song")
    sheet.getCellByPosition(1, 7).setValue(8000)       # B8: sample rate
    sheet.getCellByPosition(1, 8).setValue(1)          # B9: render one order
    sheet.getCellByPosition(1, 9).setString(str(wav_path))  # B10: WAV path
    sheet.getCellByPosition(1, 22).setValue(0)         # B23: first order = pattern 0


def verify_wav(path: Path) -> tuple[int, bool]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if channels != 1 or sample_width != 2 or rate != 8000:
        raise AssertionError(f"Unexpected WAV format: {channels}ch/{sample_width}B/{rate}Hz")
    return len(frames) // 2, any(byte != 0 for byte in frames)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--soffice", default="soffice")
    args = parser.parse_args()

    workbook = args.workbook.resolve()
    if not workbook.is_file():
        raise SystemExit(f"Workbook not found: {workbook}")

    with tempfile.TemporaryDirectory(prefix="spreadtracker-calc-") as temp_dir:
        temp = Path(temp_dir)
        profile = temp / "profile"
        scripts = profile / "user" / "Scripts" / "python"
        scripts.mkdir(parents=True)
        shutil.copy2(MACRO_SOURCE, scripts / MACRO_SOURCE.name)
        shutil.copytree(PACKAGE_SOURCE, scripts / PACKAGE_SOURCE.name)

        # Use a private copy so an already-open GUI workbook cannot lock it.
        workbook = temp / "spreadtracker.xlsx"
        shutil.copy2(args.workbook.resolve(), workbook)

        wav_path = temp / "calc-live.wav"
        port = free_port()
        command = [
            args.soffice,
            "--headless",
            "--invisible",
            "--nologo",
            "--nodefault",
            "--norestore",
            f"-env:UserInstallation={profile.as_uri()}",
            f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ComponentContext",
        ]
        process = subprocess.Popen(command)
        desktop = None
        doc = None
        try:
            context = connect_to_libreoffice(port)
            desktop = context.ServiceManager.createInstanceWithContext(
                "com.sun.star.frame.Desktop", context
            )
            doc = desktop.loadComponentFromURL(
                uno.systemPathToFileUrl(str(workbook)),
                "_blank",
                0,
                (property_value("Hidden", True), property_value("ReadOnly", False)),
            )
            if doc is None:
                raise RuntimeError("LibreOffice could not load the workbook")

            set_song_cells(doc, wav_path)
            provider = doc.getScriptProvider()
            script = provider.getScript(SCRIPT_URL)
            result = script.invoke((doc,), (), ())
            status = result[0] if isinstance(result, tuple) and result else result

            if not wav_path.is_file():
                raise RuntimeError(f"RenderWav did not create {wav_path}: {status}")

            sample_count, non_silent = verify_wav(wav_path)
            if not non_silent:
                raise AssertionError("Rendered WAV is silent")

            print(f"OK: LibreOffice Calc macro rendered {sample_count} samples")
            print(f"Render status: {status}")
            print(f"WAV: {wav_path}")

            try:
                play_result = provider.getScript(PLAY_SCRIPT_URL).invoke((doc,), (), ())
                play_status = (
                    play_result[0]
                    if isinstance(play_result, tuple) and play_result
                    else play_result
                )
                if not str(play_status).startswith(("Playing:", "Rendered,")):
                    raise AssertionError(f"Unexpected RenderAndPlay status: {play_status}")
                print(f"Play status: {play_status}")
            finally:
                provider.getScript(STOP_SCRIPT_URL).invoke((), (), ())
        finally:
            if doc is not None:
                try:
                    doc.close(True)
                except Exception:
                    pass
            if desktop is not None:
                try:
                    desktop.terminate()
                except Exception:
                    pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
