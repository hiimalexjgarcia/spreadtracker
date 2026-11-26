# AGENTS.md

Instructions for agents working in this repository.

## Project model

Spreadtracker is a spreadsheet-fronted ProTracker-style MOD player. A classic
4-channel `.mod` file is converted into the shared `.xlsx` worksheet layout;
Excel reads it through VBA and LibreOffice Calc reads it through Python UNO.
Both frontends must stay compatible with the same workbook structure.

The workbook in `workbook/` is a generated artifact. Do not hand-edit it or
patch it with a script. Change the generator or the model, regenerate it, and
verify it.

## Behavioral priorities

1. **Strict ProTracker 2.3D behavior wins.** Use
   [pt2-clone](https://github.com/8bitbubsy/pt2-clone) as the behavioral
   reference. OpenMPT comparisons are diagnostic only; do not add a
   compatibility-mode switch or choose OpenMPT behavior when it conflicts with
   PT2.
2. **Keep Python and VBA renderers mirrored.** A change to effect timing,
   periods, mixing, or sample playback in `spreadtracker/render.py` normally
   needs the corresponding change in `vba/AudioRender.bas`, plus regression
   tests for both the shared renderer and the effects in question.
3. **The worksheet layout is an interface.** Coordinates, named ranges, cell
   text, and base64 sample encoding are shared by `tools/build_workbook.py`,
   `spreadtracker/calc.py`, `tests/fake_uno.py`, and `vba/TrackerCore.bas`.
   Change all consumers together and rebuild/verify the workbook.
4. **Reference fixtures are evidence.** The mono WAVs in
   `tests/fixtures/pt2/` are committed pt2-clone renders. Do not edit them
   manually or refresh them just to make a failing comparison pass. Refresh
   only after an intentional renderer change has been reviewed.

## Subtle behavior to preserve

These decisions came from targeted PT2 comparisons and have regression
coverage. Treat them as intentional unless there is clear new evidence:

- Tick-driven sequencing: continuous effects run once per tick, not once per
  output frame.
- Signed 8-bit sample decoding, linear interpolation, and a short attack ramp.
- PT2's finetuned period tables: map the base note index into the finetuned
  row; do not apply a fractional finetune multiplier.
- Vibrato/tremolo use PT2's 32-step oscillator table with an 8-bit phase and
  divisor `128`. Phase advances only on rows with an active command, and
  `4xy` speed/depth remain in memory. `6xy` only applies the volume slide.
- `4xx`/`7xx` phase-hold bits (`E4x`/`E7x`) must not reset LFO phase. `E9x`
  retriggers sample position but preserves LFO phase.
- `EDx` loads note data and the new loop pointer at tick 0; the old current
  block keeps playing until the DMA retrigger on tick x. `ED0` never
  retriggers.
- `Dxx` uses BCD row values and wraps values above row 63 to row 0.
- `Fxx <= 0x1F` sets speed immediately; `Fxx > 0x1F` changes BPM after a
  one-tick delay.
- `5xx` uses `xx` only for volume slide; portamento speed and target come from
  prior `3xx`/`5xx` memory. Tick 0 loads the target without a volume slide;
  later ticks apply both.
- MOD repeat lengths of two bytes or fewer are intentionally treated as
  one-shot. Strict PT2 looping of a one-word Paula block is a known gap; it
  lowers the `gslinger.mod` correlation and should not be "fixed" casually.

## Repository map

```text
spreadtracker/
  model.py    # MOD parser, period tables, Song data model, cell format
  render.py   # shared Python WAV renderer
  calc.py     # Calc/UNO worksheet reader and macro logic
  audio.py    # optional cross-platform playback
calc/Spreadtracker.py        # Calc macro entry points
vba/TrackerCore.bas          # Excel worksheet and sample parsing
vba/AudioRender.bas          # Excel renderer and playback
tools/build_workbook.py      # .xlsx generator and effect documentation
tools/verify_workbook.py     # generated-workbook checks
tools/verify_calc_live.py    # optional headless Calc/UNO macro check
tools/compare_pt2_render.py  # pt2-clone reference comparison/refresh
tests/fixtures/*.mod         # source modules
tests/fixtures/pt2/          # committed mono pt2 reference WAVs + manifest
workbook/spreadtracker.xlsx  # generated workbook
```

## Development commands

Use the standard library `unittest`; do not make `pytest` a requirement.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

python -m unittest discover -s tests -q
git diff --check
```

To rebuild and verify the workbook after parser, layout, or effects-table
changes:

```bash
python tools/build_workbook.py \
  --mod tests/fixtures/space_debris.mod \
  --out workbook/spreadtracker.xlsx

python tools/verify_workbook.py \
  --mod tests/fixtures/space_debris.mod \
  --workbook workbook/spreadtracker.xlsx

python -m unittest tests.test_calc_render tests.test_effects
```

For a pt2 comparison, use the committed references by default:

```bash
python tools/compare_pt2_render.py \
  --json /tmp/spreadtracker-pt2-results.json
```

Only refresh references intentionally. This needs a local pt2-clone checkout,
CMake, a C compiler, and SDL2 headers; the tool patches a copy, never the
checkout itself:

```bash
python tools/compare_pt2_render.py \
  --refresh-pt2 \
  --pt2-src /path/to/pt2-clone
```

`tests/fixtures/pt2/manifest.json` records sample rate, filter model, frame
counts, SHA-256 digests, and source commit. Keep it in sync with the WAVs.
Comparisons are row-aligned because pt2-clone's fixed-point BPM table drifts
against a constant-rate renderer; raw zero-lag correlation is not a useful pass
criterion.

The live Calc check needs LibreOffice and `python3-uno`, and should use the
system Python rather than the virtual environment:

```bash
python3 tools/verify_calc_live.py
```

## Change checklist

- **MOD parser or model:** update model tests; rebuild and verify the workbook.
- **Effects or mixer:** add synthetic effect tests; mirror Python/VBA; run the
  pt2 comparison; update `README.md` and the `EFFECT_INFO` tables in
  `tools/build_workbook.py` if rendered status changed.
- **Workbook layout:** update generator, Calc reader, fake UNO fixture, VBA
  reader, tests, and the regenerated `.xlsx` together.
- **New MOD fixture:** add its pt2 reference WAV and manifest entry, then run
  the full comparison.
- **Renderer constants or borrowed tables:** keep Python and VBA values equal
  and update `THIRD_PARTY_NOTICES.md` when adding third-party table data.

## Style

- Python code uses `from __future__ import annotations`, type hints,
  `argparse`, `pathlib`, and `unittest`.
- Keep CLI defaults and workbook paths relative to the repository root.
- Prefer small, behavior-driven changes with a focused regression test over
  broad rewrites of the renderer.
- Comments in both renderers should explain PT2 quirks that are easy to
  "simplify" incorrectly.
- Generated WAVs outside `tests/fixtures/pt2/` and the scratch `.pt2-compare/`
  directory must not be committed.
