<img src="assets/icon.svg" width="64" height="64" alt="">

# Spreadtracker

A prototype **spreadsheet-based Protracker-style music tracker**. A classic
4-channel `.mod` file is parsed into worksheets, and both an Excel VBA frontend
and a LibreOffice Calc Python frontend can read those worksheets back, render a
WAV, and play it.

## Current prototype

- [workbook/spreadtracker.xlsx](workbook/spreadtracker.xlsx) — generated workbook (opens in Excel or Calc)
- [spreadtracker/model.py](spreadtracker/model.py) — shared MOD parser and data model
- [spreadtracker/render.py](spreadtracker/render.py) — shared 4-channel WAV renderer
- [spreadtracker/calc.py](spreadtracker/calc.py) — Calc/UNO worksheet reader and macro logic
- [spreadtracker/audio.py](spreadtracker/audio.py) — optional cross-platform playback
- [calc/Spreadtracker.py](calc/Spreadtracker.py) — Calc Python macro entry points
- [vba/TrackerCore.bas](vba/TrackerCore.bas) — Excel worksheet parsing, cell decoding, base64 sample decoding
- [vba/AudioRender.bas](vba/AudioRender.bas) — Excel 4-channel WAV renderer and playback entry points
- [tests/fixtures/space_debris.mod](tests/fixtures/space_debris.mod) — sample 4-channel Protracker module
- [tests/test_calc_render.py](tests/test_calc_render.py) — headless Calc-path verification
- [tools/build_workbook.py](tools/build_workbook.py) — workbook generator
- [tools/verify_workbook.py](tools/verify_workbook.py) — generated-workbook checks
- [tools/verify_calc_live.py](tools/verify_calc_live.py) — live headless Calc/UNO macro test

The workbook is `.xlsx` by design: VBA cannot be embedded by the generator. To
play audio, import the two `.bas` files into the VBA editor and save the result
as `.xlsm`.

## Workbook layout

| Sheet | Contents |
|---|---|
| `StartHere` | Overview, usage steps, and current limitations |
| `Song` | Tempo, speed, sample rate, render length, WAV path, order list with pattern links |
| `Patterns` | All 64-row patterns stacked in 66-row blocks; columns B:E are channels 1-4 |
| `Instruments` | The 31 classic MOD sample headers plus usage counts |
| `Effects` | Protracker effect code reference and renderer status |
| `SampleData` | Hidden sheet containing embedded sample bytes as base64 chunks |

A pattern cell looks like `C-4 07 C40`: note, two-digit **decimal** sample
number, and effect code with two hex parameter digits. `--- -- ...` means an
empty cell.

The renderer currently supports:

- note triggers and instrument/sample volume
- signed 8-bit MOD sample decoding
- `0xx` arpeggio
- `9xx` sample offset
- `Axx` volume slide
- `Cxx` set volume
- static tempo/speed from the `Song` sheet
- 4-channel mono mix at 16-bit PCM, nearest-neighbour resampling
- Win32 `sndPlaySound` playback from Excel
- Python-UNO playback from Calc, with `sounddevice`/`pygame` or a system player fallback

Portamento, vibrato, tremolo, pattern jump/break, and live `Fxx` tempo changes
are parsed into the grid but are **not yet rendered**.

## Using it

The workbook data is shared by both frontends. Adjust `Tempo (BPM)`,
`Speed (ticks/row)`, `Output sample rate`, and `Render orders` on the `Song`
sheet in either application.

### Windows desktop Excel

1. Open `workbook/spreadtracker.xlsx` in **Windows desktop Excel**.
2. Press `Alt+F11`, then `File > Import File...` and import both files in `vba/`.
3. Save as `Spreadtracker.xlsm` and enable macros when prompted.
4. Run the `RenderAndPlay` macro. Run `StopPlayback` to stop playback.

> Excel for the web cannot run VBA, and Excel for Mac cannot call `winmm.dll`.

### LibreOffice Calc

Calc uses the Python renderer, so the VBA files are not needed.

1. Copy [calc/Spreadtracker.py](calc/Spreadtracker.py) **and** the entire
   [spreadtracker/](spreadtracker/) package into your LibreOffice Python scripts
   directory:
   - Linux: `~/.config/libreoffice/4/user/Scripts/python/`
   - macOS: `~/Library/Application Support/LibreOffice/4/user/Scripts/python/`
   - Windows: `%APPDATA%\LibreOffice\4\user\Scripts\python\`
2. Optionally install the playback dependencies:
   `python -m pip install -r requirements-calc.txt`. Rendering works without
   them; without a Python audio backend, Spreadtracker falls back to `aplay`,
   `afplay`, `ffplay`, or Windows Media Player.
3. Quit and restart LibreOffice after copying or updating the macro; the macro
   list is cached.
4. Open `workbook/spreadtracker.xlsx` in Calc and choose
   `Tools > Macros > Run Macro...`, then expand
   `My Macros > Spreadtracker > Spreadtracker.py` and select `RenderWav`,
   `RenderAndPlay`, or `StopPlayback`.
5. `RenderWav` renders without playing, and `StopPlayback` stops the last
   playback. Status is written to `StartHere!D3`.

> Some Linux distributions require the LibreOffice Python script provider
> package before Python macros appear. Calc can open the existing `.xlsx`
> directly; a native `.ods` generator is not included yet.

## Rebuilding

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python tools/build_workbook.py \
  --mod tests/fixtures/space_debris.mod \
  --out workbook/spreadtracker.xlsx

python tools/verify_workbook.py \
  --mod tests/fixtures/space_debris.mod \
  --workbook workbook/spreadtracker.xlsx

python -m unittest tests.test_calc_render
```

To verify the real Calc macro path, with LibreOffice and `python3-uno`
installed, use your system Python rather than the virtual environment:

```bash
python3 tools/verify_calc_live.py
```

It starts a private headless LibreOffice instance, installs the macro into a
temporary user profile, invokes `RenderWav`, `RenderAndPlay`, and
`StopPlayback`, then verifies that the WAV is non-silent.

## Architecture

```text
spreadtracker/
  model.py    # MOD parser, period tables, Song data model
  render.py   # shared WAV renderer
  calc.py     # Calc/UNO worksheet reader and macro logic
  audio.py    # optional cross-platform playback
vba/          # Excel macro source
calc/         # Calc macro entry points
workbook/     # generated .xlsx
```

Both frontends read the same worksheet layout and feed the same Python-style
song model into their renderer. The Excel path currently uses its VBA renderer;
the Calc path uses the shared Python renderer.

## Roadmap

- Render the remaining Protracker effects (portamento, vibrato, tremolo).
- Handle `Fxx` speed/tempo changes and `Bxx`/`Dxx` song navigation.
- Add per-channel volume/tempo automation and stereo panning.
- Add an in-workbook editor mode: edit a selected pattern, then re-render.
- Add a native `.ods` workbook generator for Calc.
- Optional: write `.mod`/`.xm`/`.it` files from worksheet data.
- Optional: let Excel call the shared Python renderer instead of duplicating it in VBA.
