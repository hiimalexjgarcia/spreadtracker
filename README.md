<img src="assets/icon.svg" width="64" height="64" alt="">

# Spreadtracker

A prototype **spreadsheet-based Protracker-style music tracker**. A classic
4-channel `.mod` file is parsed into worksheets, and VBA source is provided to
read those worksheets back, render a WAV, and play it from Excel on Windows.

## Current prototype

- [workbook/spreadtracker.xlsx](workbook/spreadtracker.xlsx) — generated workbook
- [vba/TrackerCore.bas](vba/TrackerCore.bas) — worksheet parsing, cell decoding, base64 sample decoding
- [vba/AudioRender.bas](vba/AudioRender.bas) — 4-channel WAV renderer and playback entry points
- [tests/fixtures/space_debris.mod](tests/fixtures/space_debris.mod) — sample 4-channel Protracker module
- [tools/build_workbook.py](tools/build_workbook.py) — MOD parser and workbook generator
- [tools/verify_workbook.py](tools/verify_workbook.py) — generated-workbook checks

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
- `9xx` sample offset
- `Cxx` set volume
- static tempo/speed from the `Song` sheet
- 4-channel mono mix at 16-bit PCM, nearest-neighbour resampling
- Win32 `sndPlaySound` playback from inside Excel

Effects such as slides, vibrato, tremolo, arpeggio, pattern jump/break, and
live `Fxx` tempo changes are parsed into the grid but are **not yet rendered**.

## Using it

1. Open `workbook/spreadtracker.xlsx` in **Windows desktop Excel**.
2. Press `Alt+F11`, then `File > Import File...` and import both files in `vba/`.
3. Save as `Spreadtracker.xlsm` and enable macros when prompted.
4. Optionally adjust `Tempo (BPM)`, `Speed (ticks/row)`, `Output sample rate`,
   and `Render orders` on the `Song` sheet.
5. Run the `RenderAndPlay` macro. It writes the WAV next to the workbook by
   default and starts asynchronous playback. Run `StopPlayback` to stop it.

> Excel for the web cannot run VBA, and Excel for Mac cannot call `winmm.dll`.
> The generator and spreadsheet data are cross-platform; playback is Windows-only.

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
```

## Roadmap

- Render the remaining Protracker effects (slides, vibrato, tremolo, arpeggio).
- Handle `Fxx` speed/tempo changes and `Bxx`/`Dxx` song navigation.
- Add per-channel volume/tempo automation and stereo panning.
- Add an in-workbook editor mode: edit a selected pattern, then re-render.
- Optional: write `.mod`/`.xm`/`.it` files from worksheet data.
- Optional: swap the Win32 renderer for a WASAPI-based VBA engine.
