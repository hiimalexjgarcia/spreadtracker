"""Compare Spreadtracker renders against committed pt2-clone fixtures.

By default this tool reads the mono pt2-clone WAVs in
``tests/fixtures/pt2/``. To regenerate them, pass ``--refresh-pt2`` and a local
pt2-clone checkout with ``--pt2-src``. Regeneration copies the checkout into a
work directory, adds a small headless ``PT2_RENDER_OUT`` hook to that copy,
builds it, and renders every fixture through pt2-clone's own MOD2WAV renderer.

The pt2-clone renderer uses a fixed-point BPM table and drifts slowly against a
constant-rate renderer. The tool therefore estimates that drift from anchor
windows and compares each pattern row at its locally aligned frame offset.

Usage::

    python3 tools/compare_pt2_render.py
    python3 tools/compare_pt2_render.py --refresh-pt2 --pt2-src /path/to/pt2-clone
"""

from __future__ import annotations

import argparse
import array
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import wave
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from spreadtracker.model import RenderSettings, parse_mod
from spreadtracker.render import render_song

REFERENCE_DIR = REPO_ROOT / "tests" / "fixtures" / "pt2"
MANIFEST_NAME = "manifest.json"

HEADLESS_MARKER = "PT2_RENDER_OUT"
HEADLESS_VERSION = "2"
HEADLESS_INCLUDE = '#include "pt2_mod2wav.h"\n'
HEADLESS_HOOK = r'''
	// headless MOD2WAV mode for automated comparisons
	const char *renderOut = getenv("PT2_RENDER_OUT");
	if (renderOut != NULL && renderOut[0] != '\0' && song->loaded)
	{
		const char *rateEnv = getenv("PT2_RENDER_RATE");
		if (rateEnv != NULL)
		{
			int32_t rate = atoi(rateEnv);
			if (rate >= 22050 && rate <= 192000)
				config.mod2WavOutputFreq = rate;
		}
		const char *modelEnv = getenv("PT2_RENDER_MODEL");
		if (modelEnv != NULL && strncmp(modelEnv, "A500", 4) == 0)
			config.amigaModel = MODEL_A500;
		else if (modelEnv != NULL && strncmp(modelEnv, "A1200", 5) == 0)
			config.amigaModel = MODEL_A1200;
		audio.amigaModel = config.amigaModel;

		audioSetStereoSeparation(0); // mono output for easier comparison
		editor.songPlaying = false;

		char *renderFilename = strdup(renderOut);
		if (mod2WavRender(renderFilename))
		{
			while (editor.mod2WavOngoing)
			{
				SDL_Delay(10);
				updateMod2WavDialog();
			}
		}
		free(renderFilename);

		cleanUp();
		SDL_Quit();
		return 0;
	}
'''.strip("\n")


@dataclass
class Anchor:
    frame: int
    lag: int
    correlation: float


@dataclass
class FixtureResult:
    name: str
    frames: int
    rows: int
    zero_lag_correlation: float
    mean_row_correlation: float
    weighted_row_correlation: float
    lag_min: int
    lag_max: int
    drift_ppm: float
    pt2_wav: str
    spreadtracker_wav: str


def load_manifest(reference_dir: Path) -> dict:
    """Load the committed pt2 reference manifest, or return an empty dict."""
    path = reference_dir / MANIFEST_NAME
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def manifest_matches(args: argparse.Namespace, manifest: dict) -> bool:
    """Check that cached references were rendered with these settings."""
    return (
        manifest.get("sample_rate") == args.sample_rate
        and manifest.get("amiga_model") == args.amiga_model
        and manifest.get("channels") == 1
        and manifest.get("sample_width") == 2
    )


def cached_reference(
    reference_dir: Path,
    manifest: dict,
    module: Path,
    args: argparse.Namespace,
) -> Path | None:
    """Return the cached pt2 WAV for ``module`` when it is usable."""
    if not manifest_matches(args, manifest):
        return None
    entry = manifest.get("files", {}).get(module.name)
    if not isinstance(entry, dict):
        return None

    path = reference_dir / f"{module.stem}.wav"
    if not path.is_file():
        return None

    with wave.open(str(path), "rb") as wav:
        if (
            wav.getframerate() != args.sample_rate
            or wav.getnchannels() != 1
            or wav.getsampwidth() != 2
            or wav.getnframes() != entry.get("frames")
        ):
            return None

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path if digest == entry.get("sha256") else None


def write_manifest(
    reference_dir: Path,
    args: argparse.Namespace,
    modules: list[Path],
    paths: list[Path],
    source: Path | None,
) -> dict:
    """Write a manifest describing freshly refreshed pt2 reference WAVs."""
    existing = load_manifest(reference_dir)
    old_files = existing.get("files", {})
    files = dict(old_files) if isinstance(old_files, dict) else {}
    for module, path in zip(modules, paths):
        with wave.open(str(path), "rb") as wav:
            frames = wav.getnframes()
        files[module.name] = {
            "frames": frames,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }

    commit = None
    if source is not None:
        result = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()

    manifest = {
        "sample_rate": args.sample_rate,
        "amiga_model": args.amiga_model,
        "channels": 1,
        "sample_width": 2,
        "source": {"project": "8bitbubsy/pt2-clone", "commit": commit},
        "files": files,
    }
    (reference_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def read_wav_mono(path: Path) -> array.array:
    """Read a 16-bit WAV, downmixing stereo to mono."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError(f"{path}: expected 16-bit PCM WAV")
        channels = wav.getnchannels()
        raw = array.array("h")
        raw.frombytes(wav.readframes(wav.getnframes()))
    if sys.byteorder == "big":
        raw.byteswap()
    if channels == 1:
        return raw
    if channels != 2:
        raise ValueError(f"{path}: unsupported channel count {channels}")
    return array.array(
        "h", ((raw[i] + raw[i + 1]) // 2 for i in range(0, len(raw) - 1, 2))
    )


def write_wav_mono(path: Path, samples: array.array, sample_rate: int) -> None:
    """Write 16-bit mono PCM samples to ``path``."""
    values = array.array("h", samples)
    if sys.byteorder == "big":
        values.byteswap()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(values.tobytes())


def pearson(left: array.array | list[int], right: array.array | list[int]) -> float:
    """Cheap Pearson correlation over the overlapping prefix."""
    count = min(len(left), len(right))
    if count < 8:
        return 0.0
    a = list(left[:count])
    b = list(right[:count])
    mean_a = sum(a) / count
    mean_b = sum(b) / count
    a = [value - mean_a for value in a]
    b = [value - mean_b for value in b]
    energy_a = sum(value * value for value in a)
    energy_b = sum(value * value for value in b)
    if energy_a == 0 or energy_b == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / math.sqrt(energy_a * energy_b)


def best_lag(
    reference: array.array,
    rendered: array.array,
    start: int,
    width: int,
    max_lag: int,
    stride: int,
) -> tuple[float, int]:
    """Find the reference delay that best matches a rendered window."""
    a = list(rendered[start : start + width : stride])
    if len(a) < 8:
        return 0.0, 0
    mean_a = sum(a) / len(a)
    centered_a = [value - mean_a for value in a]
    energy_a = sum(value * value for value in centered_a)
    if energy_a == 0:
        return 0.0, 0

    def score(lag: int) -> float:
        ref_start = start + lag
        if ref_start < 0:
            return -2.0
        b = list(reference[ref_start : ref_start + width : stride])
        if len(b) < 8:
            return -2.0
        mean_b = sum(b) / len(b)
        centered_b = [value - mean_b for value in b]
        energy_b = sum(value * value for value in centered_b)
        if energy_b == 0:
            return -2.0
        return sum(x * y for x, y in zip(centered_a, centered_b)) / math.sqrt(
            energy_a * energy_b
        )

    # Coarse-to-fine search: the drift is smooth, so this is much cheaper than
    # scoring every one-frame lag over a multi-minute fixture.
    coarse_step = 4
    best_correlation, best_offset = max(
        (score(lag), lag) for lag in range(-max_lag, max_lag + 1, coarse_step)
    )
    for lag in range(
        max(-max_lag, best_offset - coarse_step),
        min(max_lag, best_offset + coarse_step) + 1,
    ):
        value = score(lag)
        if value > best_correlation:
            best_correlation, best_offset = value, lag
    return best_correlation, best_offset


def fit_drift(anchors: list[Anchor]) -> tuple[float, float]:
    """Weighted least-squares fit of lag = intercept + slope * frame."""
    if not anchors:
        return 0.0, 0.0
    if len(anchors) == 1:
        return float(anchors[0].lag), 0.0

    weights = [max(anchor.correlation, 0.01) ** 2 for anchor in anchors]
    total = sum(weights)
    mean_x = sum(weight * anchor.frame for weight, anchor in zip(weights, anchors)) / total
    mean_y = sum(weight * anchor.lag for weight, anchor in zip(weights, anchors)) / total
    spread = sum(
        weight * (anchor.frame - mean_x) ** 2 for weight, anchor in zip(weights, anchors)
    )
    if spread == 0:
        return mean_y - mean_x, 0.0
    slope = sum(
        weight * (anchor.frame - mean_x) * (anchor.lag - mean_y)
        for weight, anchor in zip(weights, anchors)
    ) / spread
    return mean_y - slope * mean_x, slope


def apply_headless_hook(source: Path) -> None:
    """Patch the copied pt2-clone main file; safe to call twice."""
    main = source / "src" / "pt2_main.c"
    text = main.read_text(encoding="utf-8")
    if HEADLESS_MARKER in text:
        return

    include_anchor = '#include "pt2_textedit.h"\n'
    if include_anchor not in text:
        raise RuntimeError(f"Cannot find include anchor in {main}")
    text = text.replace(include_anchor, include_anchor + HEADLESS_INCLUDE, 1)

    render_anchor = "\n\tdisplayMainScreen();"
    if render_anchor not in text:
        raise RuntimeError(f"Cannot find render anchor in {main}")
    text = text.replace(render_anchor, "\n" + HEADLESS_HOOK + render_anchor, 1)
    main.write_text(text, encoding="utf-8")


def prepare_pt2(source: Path, work: Path, rebuild: bool, jobs: int) -> Path:
    """Copy, patch, and build pt2-clone in the work directory."""
    cmake = shutil.which("cmake")
    if cmake is None:
        raise RuntimeError("cmake is required to build pt2-clone")

    source = source.resolve()
    if not (source / "src" / "pt2_mod2wav.c").is_file():
        raise RuntimeError(f"{source} does not look like a pt2-clone checkout")

    copy = work / "pt2-src"
    binary = copy / "release" / "other" / "pt2-clone"
    marker = work / "pt2-source.txt"
    expected_marker = f"{source}\n{HEADLESS_VERSION}"
    reusable = (
        marker.is_file()
        and marker.read_text(encoding="utf-8").strip() == expected_marker
        and binary.is_file()
        and not rebuild
    )
    if reusable:
        return binary

    if copy.exists():
        shutil.rmtree(copy)
    shutil.copytree(
        source,
        copy,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    apply_headless_hook(copy)

    build = work / "pt2-build"
    configure = subprocess.run(
        [cmake, "-S", str(copy), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release"],
        capture_output=True,
        text=True,
    )
    if configure.returncode != 0:
        raise RuntimeError(f"cmake configure failed:\n{configure.stderr}")
    compile_result = subprocess.run(
        [cmake, "--build", str(build), "-j", str(jobs)],
        capture_output=True,
        text=True,
    )
    if compile_result.returncode != 0:
        raise RuntimeError(f"cmake build failed:\n{compile_result.stderr}")
    if not binary.is_file():
        raise RuntimeError(f"pt2-clone binary was not created at {binary}")

    marker.write_text(expected_marker + "\n", encoding="utf-8")
    return binary


def render_pt2(
    binary: Path,
    module: Path,
    output: Path,
    sample_rate: int,
    amiga_model: str,
) -> None:
    """Render one module with the patched headless pt2-clone."""
    output = output.resolve()
    if output.exists():
        output.unlink()  # mod2WavRender asks before overwriting, which blocks headless.
    output.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        SDL_VIDEODRIVER="dummy",
        SDL_AUDIODRIVER="dummy",
        SDL_RENDER_DRIVER="dummy",
        PT2_RENDER_OUT=str(output),
        PT2_RENDER_RATE=str(sample_rate),
        PT2_RENDER_MODEL=amiga_model,
    )
    result = subprocess.run(
        [str(binary), str(module.resolve())],
        cwd=binary.parent,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(
            f"pt2-clone render failed for {module.name}:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def compare_fixture(
    module: Path,
    pt2_path: Path,
    work: Path,
    args: argparse.Namespace,
) -> FixtureResult:
    mod = parse_mod(module)
    song = mod.to_song(
        RenderSettings(orders=mod.song_length, sample_rate=args.sample_rate)
    )

    ours_path = work / "spreadtracker" / f"{module.stem}.wav"
    ours_path.parent.mkdir(parents=True, exist_ok=True)
    row_log: list[tuple[int, int, int, int]] = []
    render_song(song, ours_path, row_log=row_log)

    rendered = read_wav_mono(ours_path)
    reference = read_wav_mono(pt2_path)
    if len(rendered) < args.min_row_frames * 2:
        raise RuntimeError(f"{module.name}: Spreadtracker render is too short")

    stride = max(1, args.stride)
    anchors: list[Anchor] = []
    anchor_step = args.sample_rate * args.anchor_interval
    anchor_width = args.sample_rate * args.anchor_window
    for frame in range(0, min(len(rendered), len(reference)) - anchor_width, anchor_step):
        correlation, lag = best_lag(
            reference, rendered, frame, anchor_width, args.max_lag, stride
        )
        anchors.append(Anchor(frame=frame, lag=lag, correlation=correlation))

    intercept, slope = fit_drift(anchors)
    row_correlations: list[float] = []
    row_weights: list[int] = []
    refined_lags: list[int] = []

    for index, (_, _, _, start) in enumerate(row_log):
        end = row_log[index + 1][3] if index + 1 < len(row_log) else len(rendered)
        length = end - start
        if length < args.min_row_frames:
            continue
        predicted = round(intercept + slope * start)
        best_correlation = -2.0
        best_lag_value = predicted
        for lag in range(predicted - args.refine, predicted + args.refine + 1):
            ref_start = start + lag
            if ref_start < 0 or ref_start + length > len(reference):
                continue
            value = pearson(
                reference[ref_start : ref_start + length : stride],
                rendered[start:end:stride],
            )
            if value > best_correlation:
                best_correlation = value
                best_lag_value = lag
        if best_correlation <= -2.0:
            continue
        row_correlations.append(best_correlation)
        row_weights.append(length)
        refined_lags.append(best_lag_value)

    if not row_correlations:
        raise RuntimeError(f"{module.name}: no comparable rows found")

    total_weight = sum(row_weights)
    return FixtureResult(
        name=module.name,
        frames=len(rendered),
        rows=len(row_correlations),
        zero_lag_correlation=pearson(reference[::stride], rendered[::stride]),
        mean_row_correlation=sum(row_correlations) / len(row_correlations),
        weighted_row_correlation=sum(
            value * weight for value, weight in zip(row_correlations, row_weights)
        )
        / total_weight,
        lag_min=min(refined_lags),
        lag_max=max(refined_lags),
        drift_ppm=slope * 1_000_000.0,
        pt2_wav=str(pt2_path),
        spreadtracker_wav=str(ours_path),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pt2-src",
        default=os.environ.get("PT2_SRC"),
        help="pt2-clone checkout; only needed with --refresh-pt2 or --rebuild",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=REFERENCE_DIR,
        help="Directory holding committed mono pt2-clone WAVs and manifest.json",
    )
    parser.add_argument(
        "--mod",
        action="append",
        type=Path,
        help="MOD file to compare; repeat for several files (default: all fixtures)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=REPO_ROOT / ".pt2-compare",
        help="Directory for the patched pt2-clone copy and rendered WAVs",
    )
    parser.add_argument("--sample-rate", type=int, default=22050)
    parser.add_argument("--amiga-model", choices=("A500", "A1200"), default="A1200")
    parser.add_argument("--max-lag", type=int, default=500, help="Anchor lag search in frames")
    parser.add_argument("--refine", type=int, default=3, help="Per-row lag refinement in frames")
    parser.add_argument("--anchor-interval", type=int, default=10, help="Seconds between anchors")
    parser.add_argument("--anchor-window", type=int, default=2, help="Anchor window in seconds")
    parser.add_argument("--stride", type=int, default=8, help="Frame decimation for correlations")
    parser.add_argument("--min-row-frames", type=int, default=200)
    parser.add_argument("--jobs", type=int, default=4, help="cmake build parallelism")
    parser.add_argument("--rebuild", action="store_true", help="Force a fresh pt2-clone build")
    parser.add_argument(
        "--refresh-pt2",
        action="store_true",
        help="Re-render and overwrite the committed pt2 reference WAVs",
    )
    parser.add_argument("--json", type=Path, help="Also write results as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.work_dir = args.work_dir.expanduser().resolve()
    args.reference_dir = args.reference_dir.expanduser().resolve()
    if args.sample_rate < 22050:
        raise SystemExit("--sample-rate must be at least 22050 for pt2-clone MOD2WAV")

    modules = args.mod or sorted((REPO_ROOT / "tests" / "fixtures").glob("*.mod"))
    if not modules:
        raise SystemExit("No MOD files found")

    args.work_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.reference_dir)
    cached = [cached_reference(args.reference_dir, manifest, module, args) for module in modules]
    force_render = args.refresh_pt2 or args.rebuild

    if not force_render and all(cached):
        pt2_paths = [path for path in cached if path is not None]
        print(f"Using committed pt2 references from {args.reference_dir}")
    else:
        if args.pt2_src is None:
            raise SystemExit(
                "Cached pt2 references are missing or use different settings; "
                "provide --pt2-src or set PT2_SRC to re-render them"
            )
        source = Path(args.pt2_src).resolve()
        binary = prepare_pt2(source, args.work_dir, args.rebuild, args.jobs)
        rendered_paths: list[Path] = []
        for module in modules:
            pt2_path = args.work_dir / "pt2" / f"{module.stem}.wav"
            render_pt2(binary, module, pt2_path, args.sample_rate, args.amiga_model)
            rendered_paths.append(pt2_path)

        if args.refresh_pt2:
            args.reference_dir.mkdir(parents=True, exist_ok=True)
            mono_paths: list[Path] = []
            for module, path in zip(modules, rendered_paths):
                target = args.reference_dir / f"{module.stem}.wav"
                write_wav_mono(target, read_wav_mono(path), args.sample_rate)
                mono_paths.append(target)
            write_manifest(args.reference_dir, args, modules, mono_paths, source)
            pt2_paths = mono_paths
            print(f"Refreshed committed pt2 references in {args.reference_dir}")
        else:
            pt2_paths = rendered_paths

    results = [
        compare_fixture(module, pt2_path, args.work_dir, args)
        for module, pt2_path in zip(modules, pt2_paths)
    ]

    print(f"{'fixture':18s} {'rows':>5s} {'zero':>9s} {'row-mean':>9s} {'row-weighted':>13s} {'lag':>10s} {'drift ppm':>10s}")
    for result in results:
        print(
            f"{result.name:18s} {result.rows:5d} "
            f"{result.zero_lag_correlation:9.6f} "
            f"{result.mean_row_correlation:9.6f} "
            f"{result.weighted_row_correlation:13.6f} "
            f"{result.lag_min:4d}..{result.lag_max:<5d} "
            f"{result.drift_ppm:10.3f}"
        )

    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([asdict(result) for result in results], indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
