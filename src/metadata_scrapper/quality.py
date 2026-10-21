"""Perceptual quality optimization for re-encoding (VMAF-driven CRF search).

Implements the 'per-title' idea popularized by Netflix: instead of a fixed
quality setting, short samples of the movie are probed, their perceptual
quality is measured with VMAF (Netflix's video quality metric, computed by
ffmpeg's libvmaf filter) and the largest HandBrake CRF that still meets the
target quality is chosen => lowest file size without visible quality loss.

The CRF search is a binary search ('bisect'): VMAF falls monotonically as
CRF rises, so ~5 probe encodes are enough to find the quality knee.

Note on score comparability: libvmaf versions measure slightly differently
(the same encode scored 95.76 on ffmpeg 5/libvmaf v2 and 94.40 on
ffmpeg 9/libvmaf v3). Decisions stay internally consistent because the
bisect measures and decides with the same local toolchain, but absolute
scores and chosen CRFs are only comparable between similar libvmaf builds.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from metadata_scrapper.encode import (
    HANDBRAKE_BINARY,
    MIN_ENCODE_SECONDS,
    durations_match,
    find_handbrake,
    probe_duration_seconds,
)
from metadata_scrapper.logger import log

FFMPEG_BINARY = "ffmpeg"

# Netflix's reference model (https://github.com/Netflix/vmaf), shipped with the
# package because old ffmpeg/libvmaf builds cannot load embedded models.
VMAF_MODEL = Path(__file__).parent / "models" / "vmaf_v0.6.1.json"

# libvmaf filter API changed with modern ffmpeg: old builds (ffmpeg < 6) take
# an explicit 'model_path', new builds take 'model=path=...' and ship embedded
# models. Detected once via the filter's option list.
_vmaf_style: str | None = None
VMAF_STYLE_LEGACY = "legacy"  # model_path=...
VMAF_STYLE_MODERN = "modern"  # model=path=...


def detect_vmaf_style(binary: str = FFMPEG_BINARY) -> str:
    """Detects which libvmaf filter API the local ffmpeg supports.

    Args:
        binary (str): The ffmpeg binary to probe.

    Returns:
        str: VMAF_STYLE_LEGACY or VMAF_STYLE_MODERN.

    Raises:
        RuntimeError: If ffmpeg is not installed or has no libvmaf filter.
    """
    global _vmaf_style
    if _vmaf_style is not None and binary == FFMPEG_BINARY:
        return _vmaf_style

    try:
        proc = subprocess.run(
            [binary, "-hide_banner", "-h", "filter=libvmaf"],
            capture_output=True,
        )
    except OSError as e:
        raise RuntimeError(
            f"ffmpeg ('{binary}') not found! Install a build with libvmaf support."
        ) from e
    options = proc.stdout.decode(errors="replace")
    if "libvmaf" not in options and "libvmaf" not in proc.stderr.decode(errors="replace"):
        raise RuntimeError("ffmpeg has no libvmaf filter! Install a build with libvmaf support.")
    _vmaf_style = VMAF_STYLE_MODERN if "model_path" not in options else VMAF_STYLE_LEGACY
    return _vmaf_style


# VMAF 95 is Netflix's 'visually transparent' zone (93 is their common
# top-tier floor); VMAF falls monotonically with rising CRF.
DEFAULT_TARGET_VMAF = 95.0
# CRF 14 is near-transparent for x265, 32 is clearly lossy for live action.
DEFAULT_CRF_RANGE = (14, 32)
DEFAULT_SAMPLE_COUNT = 3
DEFAULT_SAMPLE_SECONDS = 10.0

# a detected bar smaller than this percentage of the dimension is noise
MIN_BAR_PERCENT = 2.0
# blackness threshold for cropdetect (0-255); 24 matches ffmpeg's default
CROP_DETECT_LIMIT = 24


def parse_crop_line(text: str) -> tuple[int, int, int, int] | None:
    """Extracts the last cropdetect crop rectangle from ffmpeg output.

    Args:
        text (str): The ffmpeg stderr output containing cropdetect lines.

    Returns:
        tuple | None: The (width, height, x, y) crop rectangle, or None if
            no crop was detected.
    """
    matches = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", text)
    if not matches:
        return None
    last = matches[-1]
    return tuple(int(value) for value in last)  # type: ignore[return-value]


def intersect_crop_rects(
    rects: list[tuple[int, int, int, int]], frame: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """Intersects crop rectangles => only bars present in ALL samples.

    The intersection is safe by construction: a sample without bars
    (or with smaller bars) shrinks the crop accordingly, so no picture
    content can be removed.

    Args:
        rects (list): Detected (width, height, x, y) rectangles.
        frame (tuple): The (width, height) of the source frame.

    Returns:
        tuple | None: The (top, bottom, left, right) bars to crop, or None
            if no stable bars were detected or they are too small to matter.
    """
    if not rects:
        return None
    frame_w, frame_h = frame

    # intersection in absolute coordinates: the picture area present in all
    left = max(x for _, _, x, _ in rects)
    top = max(y for _, _, _, y in rects)
    right = min(x + w for w, _, x, _ in rects)
    bottom = min(y + h for _, h, _, y in rects)

    width = max(0, right - left)
    height = max(0, bottom - top)

    # the removed BAR sizes must be significant (2% of the dimension), noise
    # sized bars are ignored; keep even dimensions (encoder requirement)
    bar_w = frame_w - width
    bar_h = frame_h - height
    min_w = frame_w * MIN_BAR_PERCENT / 100
    min_h = frame_h * MIN_BAR_PERCENT / 100
    if (bar_w > 0 and bar_w < min_w) or (bar_h > 0 and bar_h < min_h):
        return None
    if bar_w == 0 and bar_h == 0:
        return None
    width -= width % 2
    height -= height % 2

    if width >= frame_w and height >= frame_h:
        return None
    # convert the picture rectangle into HandBrake's bar order
    bars_left = left - left % 2
    bars_top = top - top % 2
    return (
        bars_top,
        max(0, frame_h - bars_top - height),
        bars_left,
        max(0, frame_w - bars_left - width),
    )


def detect_crop_rect(
    movie: Path, workdir: Path, count: int = 3
) -> tuple[int, int, int, int] | None:
    """Detects baked-in black bars (letterbox) of a movie via cropdetect.

    Runs cropdetect on several segments and intersects the results, so only
    bars that are present throughout the movie are removed.

    Args:
        movie (Path): The source movie file.
        workdir (Path): Working directory (unused, kept for symmetry).
        count (int): The number of segments to probe.

    Returns:
        tuple | None: The (top, bottom, left, right) bars to crop, or None
            if no stable bars were detected.
    """
    from metadata_scrapper.encode import probe_duration_seconds

    duration = probe_duration_seconds(movie)
    if duration is None:
        return None

    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(movie),
        ],
        capture_output=True,
    )
    try:
        stream = next(
            s for s in json.loads(proc.stdout.decode())["streams"] if s.get("codec_type") == "video"
        )
        frame = (int(stream["width"]), int(stream["height"]))
    except (StopIteration, KeyError, ValueError, json.JSONDecodeError):
        return None

    rects: list[tuple[int, int, int, int]] = []
    for offset in sample_offsets(duration, count):
        code, err = _run(
            [
                FFMPEG_BINARY,
                "-y",
                "-v",
                "info",
                "-ss",
                f"{offset:.3f}",
                "-t",
                "2",
                "-i",
                str(movie),
                "-vf",
                f"cropdetect=limit={CROP_DETECT_LIMIT}:round=2:reset=0",
                "-f",
                "null",
                "-",
            ]
        )
        if code != 0:
            continue
        rect = parse_crop_line(err)
        if rect is not None:
            rects.append(rect)

    return intersect_crop_rects(rects, frame)


@dataclass
class SampleProbe:
    """One CRF probe: the measured quality across all samples."""

    crf: int
    vmaf: float


@dataclass
class QualityResult:
    """Result of a quality-optimized encode.

    Attributes:
        input (Path): The input file.
        output (Path): The output file.
        success (bool): True if the encode succeeded.
        target_vmaf (float): The VMAF quality floor that was requested.
        chosen_crf (int | None): The CRF selected by the bisect.
        achieved_vmaf (float | None): The worst per-sample VMAF measured at
            the chosen CRF (None if no probe was possible).
        probes (list[SampleProbe]): All CRF probes performed during the search.
        input_size (int): Size of the input file in bytes.
        output_size (int): Size of the output file in bytes.
        error (str | None): The error message if the encode failed.
    """

    input: Path
    output: Path
    success: bool
    target_vmaf: float = DEFAULT_TARGET_VMAF
    chosen_crf: int | None = None
    achieved_vmaf: float | None = None
    probes: list[SampleProbe] = field(default_factory=list)
    input_size: int = 0
    output_size: int = 0
    error: str | None = None

    @property
    def saved_percent(self) -> float:
        """Percentage of disk space saved by the encode."""
        if self.input_size <= 0 or self.output_size <= 0:
            return 0.0
        return round((1 - self.output_size / self.input_size) * 100, 2)


def _run(command: list[str], cwd: Path | None = None) -> tuple[int, str]:
    """Runs a subprocess and returns (returncode, stderr as text)."""
    proc = subprocess.run(command, capture_output=True, cwd=cwd)
    return proc.returncode, proc.stderr.decode(errors="replace").strip()


def sample_offsets(duration: float, count: int) -> list[float]:
    """Computes the sample start offsets evenly spread over the duration.

    The offsets avoid the first and last seconds (credits/lead-in) and are
    spread between 10% and 90% of the duration.

    Args:
        duration (float): The duration in seconds.
        count (int): The number of samples.

    Returns:
        list[float]: The start offsets in seconds.
    """
    if duration <= 0 or count <= 0:
        return []
    start, end = duration * 0.1, duration * 0.9
    if count == 1:
        return [start]
    step = (end - start) / (count - 1)
    return [start + i * step for i in range(count)]


def extract_samples(movie: Path, workdir: Path, count: int, seconds: float) -> list[Path]:
    """Extracts short near-lossless sample clips for probing.

    Args:
        movie (Path): The source movie file.
        workdir (Path): Directory for the sample files.
        count (int): The number of samples.
        seconds (float): The length of each sample in seconds.

    Returns:
        list[Path]: The sample files.

    Raises:
        RuntimeError: If a sample could not be extracted.
    """
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(movie),
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for '{movie}'")
    duration = float(json.loads(proc.stdout.decode())["format"]["duration"])

    samples: list[Path] = []
    for index, offset in enumerate(sample_offsets(duration, count)):
        sample = workdir / f"sample_{index}.mkv"
        code, err = _run(
            [
                FFMPEG_BINARY,
                "-y",
                "-v",
                "error",
                "-ss",
                f"{offset:.3f}",
                "-t",
                f"{seconds:.3f}",
                "-i",
                str(movie),
                "-map",
                "0:v:0",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "12",
                "-an",
                str(sample),
            ]
        )
        if code != 0 or not sample.is_file() or sample.stat().st_size == 0:
            raise RuntimeError(f"sample extraction failed at {offset:.0f}s: {err[-400:]}")
        samples.append(sample)
    return samples


def build_encode_command(
    binary: str,
    input_file: Path,
    output_file: Path,
    crf: int,
    encoder: str,
    encoder_preset: str,
    crop: tuple[int, int, int, int] | None = None,
) -> list[str]:
    """Builds the HandBrakeCLI command for a quality-driven encode.

    Unlike the fixed preset in encode.py, the quality is set via --quality
    (CRF) and the speed via --encoder-preset.

    Args:
        binary (str): Path to the HandBrakeCLI binary.
        input_file (Path): The file to re-encode.
        output_file (Path): The target file.
        crf (int): The CRF quality value.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_preset (str): The encoder speed preset, e.g. 'medium'.
        crop (tuple | None): Crop bars (top, bottom, left, right) to remove
            baked-in black bars. None keeps the source resolution.

    Returns:
        list[str]: The command as argument list.
    """
    command = [
        binary,
        "--input",
        str(input_file),
        "--output",
        str(output_file),
        "--encoder",
        encoder,
        "--encoder-preset",
        encoder_preset,
        "--quality",
        str(crf),
    ]
    if crop is not None:
        top, bottom, left, right = crop
        # HandBrake crop order is top:bottom:left:right
        command += ["--crop", f"{top}:{bottom}:{left}:{right}"]
    else:
        # keep the source resolution: HandBrake's auto-crop would letterbox
        # crops break the VMAF comparison (dimension mismatch) and change
        # the output resolution of the library
        command += ["--crop", "0:0:0:0"]
    command += [
        # keep all audio and subtitle tracks, copy audio => no quality loss
        "--all-audio",
        "--all-subtitles",
        "--aencoder",
        "copy",
        "--optimize",  # web optimized => mp4/mkv file can start streaming
    ]
    return command


def build_vmaf_command(
    distorted: Path, reference: Path, threads: int = 0, style: str | None = None
) -> list[str]:
    """Builds the ffmpeg command measuring the VMAF of an encode.

    The model file is copied to the working directory by measure_vmaf and
    referenced by bare filename => no Windows path escaping in the
    filtergraph (colons in drive letters break filter parsing). Works with
    both the legacy (<6) and modern libvmaf filter API.

    Args:
        distorted (Path): The encoded file (libvmaf 'main' input).
        reference (Path): The pristine source file (libvmaf 'reference').
        threads (int): libvmaf worker threads (0 = automatic). Meaningful
            only on modern libvmaf (v3+); legacy builds score single-threaded.
        style (str | None): Force a filter API style (legacy/modern); used by
            tests. None detects the local ffmpeg.

    Returns:
        list[str]: The command as argument list (run with cwd=workdir).
    """
    if style is None:
        style = detect_vmaf_style()
    if style == VMAF_STYLE_MODERN:
        model_option = "model=path=vmaf_v0.6.1.json"
    else:
        model_option = "model_path=vmaf_v0.6.1.json"
    return [
        FFMPEG_BINARY,
        "-y",
        "-v",
        "error",
        "-i",
        str(distorted),
        "-i",
        str(reference),
        "-lavfi",
        f"[0:v][1:v]libvmaf=log_path=vmaf.json:log_fmt=json:{model_option}:n_threads={threads}",
        "-f",
        "null",
        "-",
    ]


def parse_vmaf_log(text: str) -> float:
    """Extracts the pooled mean VMAF score from a libvmaf json log.

    Args:
        text (str): The content of the libvmaf json log file.

    Returns:
        float: The mean VMAF score.

    Raises:
        ValueError: If the log has no pooled vmaf score.
    """
    pooled = json.loads(text).get("pooled_metrics", {}).get("vmaf")
    if pooled is None or "mean" not in pooled:
        raise ValueError("libvmaf log has no pooled vmaf score")
    return float(pooled["mean"])


def measure_vmaf(distorted: Path, reference: Path, workdir: Path) -> float:
    """Measures the mean VMAF of an encode against its source.

    The main/reference input order matters: the distorted stream must be the
    first input, otherwise scores are inflated. Both input paths are resolved
    to absolute form because ffmpeg runs with cwd=workdir.

    Args:
        distorted (Path): The encoded file.
        reference (Path): The pristine source file.
        workdir (Path): Working directory for the model and log files.

    Returns:
        float: The mean VMAF score.

    Raises:
        RuntimeError: If ffmpeg/libvmaf failed or produced no score.
    """
    distorted = distorted.resolve()
    reference = reference.resolve()
    model = workdir / VMAF_MODEL.name
    shutil.copyfile(VMAF_MODEL, model)

    threads = os.cpu_count() or 0
    if detect_vmaf_style() != VMAF_STYLE_MODERN:
        # legacy libvmaf (ffmpeg < 6) is single-threaded regardless of the option
        threads = 0
    code, err = _run(build_vmaf_command(distorted, reference, threads), cwd=workdir)
    log_file = workdir / "vmaf.json"
    if code != 0 or not log_file.is_file():
        raise RuntimeError(f"libvmaf failed: {err[-400:]}")

    try:
        return parse_vmaf_log(log_file.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"libvmaf log could not be parsed: {e}") from e


def bisect_crf(check, lo: int, hi: int) -> int:
    """Finds the largest CRF whose probe still passes the quality check.

    Args:
        check (callable): Predicate check(crf) => bool, True if the quality
            target is met. Must be monotonic: True for low CRF, False for
            high CRF.
        lo (int): The lowest CRF to consider.
        hi (int): The highest CRF to consider.

    Returns:
        int: The largest passing CRF (or lo if even lo fails; the caller
            should use it anyway as the best available quality).
    """
    if not check(lo):
        return lo
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if check(mid):
            lo = mid
        else:
            hi = mid
    return lo


def probe_crf(
    samples: list[Path],
    crf: int,
    workdir: Path,
    handbrake: str,
    encoder: str,
    encoder_preset: str,
    crop: tuple[int, int, int, int] | None = None,
) -> float:
    """Encodes all samples at one CRF and returns their worst VMAF score.

    The worst (minimum) per-sample mean governs the decision so that every
    probed segment meets the target quality.

    Args:
        samples (list[Path]): The sample files.
        crf (int): The CRF quality value to probe.
        workdir (Path): Working directory for probe files.
        handbrake (str): Path to the HandBrakeCLI binary.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_preset (str): The encoder speed preset, e.g. 'medium'.
        crop (tuple | None): Crop bars applied to probes and measurements,
            so the decision matches the final encode.

    Returns:
        float: The lowest per-sample VMAF mean at this CRF.

    Raises:
        RuntimeError: If a sample encode or VMAF measurement failed.
    """
    scores: list[float] = []
    for index, sample in enumerate(samples):
        encoded = workdir / f"probe_crf{crf}_{index}.mkv"
        command = build_encode_command(
            handbrake, sample, encoded, crf, encoder, encoder_preset, crop=crop
        )
        code, err = _run(command)
        if code != 0 or not encoded.is_file() or encoded.stat().st_size == 0:
            raise RuntimeError(f"probe encode failed (crf {crf}): {err[-400:]}")
        scores.append(measure_vmaf(encoded, sample, workdir))
        encoded.unlink()
    return min(scores)


def estimate_size_factor(
    samples: list[Path],
    workdir: Path,
    handbrake: str,
    crf: int,
    encoder: str,
    encoder_preset: str,
    crop: tuple[int, int, int, int] | None = None,
) -> float:
    """Estimates the full-encode size relative to the source from one sample.

    Encodes the first sample at the given CRF and returns the size ratio
    (encoded / source). Values >= 1 mean the re-encode would not shrink the
    file => the movie is a skip candidate.

    Args:
        samples (list[Path]): The sample files.
        workdir (Path): Working directory for probe files.
        handbrake (str): Path to the HandBrakeCLI binary.
        crf (int): The CRF quality value to probe.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_preset (str): The encoder speed preset, e.g. 'medium'.
        crop (tuple | None): Crop bars applied to the probe encode.

    Returns:
        float: The size ratio of the probe encode (encoded / sample source).

    Raises:
        RuntimeError: If the probe encode failed.
    """
    sample = samples[0]
    encoded = workdir / f"size_probe_crf{crf}.mkv"
    command = build_encode_command(
        handbrake, sample, encoded, crf, encoder, encoder_preset, crop=crop
    )
    code, err = _run(command)
    if code != 0 or not encoded.is_file() or encoded.stat().st_size == 0:
        raise RuntimeError(f"size probe encode failed (crf {crf}): {err[-400:]}")
    try:
        factor = encoded.stat().st_size / sample.stat().st_size
    finally:
        encoded.unlink()
    return factor


def find_optimal_crf(
    movie: Path,
    workdir: Path,
    handbrake: str,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    encoder: str = "x265",
    encoder_preset: str = "medium",
    crop: tuple[int, int, int, int] | None = None,
) -> tuple[int, float, list[SampleProbe]]:
    """Finds the largest CRF that still meets the VMAF target on samples.

    Args:
        movie (Path): The source movie file.
        workdir (Path): Working directory for samples and probes.
        handbrake (str): Path to the HandBrakeCLI binary.
        target_vmaf (float): The VMAF quality floor to meet.
        crf_range (tuple[int, int]): The (lowest, highest) CRF to search.
        sample_count (int): The number of samples to probe.
        sample_seconds (float): The length of each sample in seconds.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_preset (str): The encoder speed preset, e.g. 'medium'.
        crop (tuple | None): Crop bars (top, bottom, left, right) applied to
            probes and the decision, matching the final encode.

    Returns:
        tuple: The chosen CRF, the VMAF measured at that CRF and all probes.

    Raises:
        RuntimeError: If sampling or probing failed.
    """
    samples = [s.resolve() for s in extract_samples(movie, workdir, sample_count, sample_seconds)]

    probes: list[SampleProbe] = []

    def check(crf: int) -> bool:
        vmaf = probe_crf(samples, crf, workdir, handbrake, encoder, encoder_preset, crop=crop)
        probes.append(SampleProbe(crf=crf, vmaf=round(vmaf, 2)))
        return vmaf >= target_vmaf

    lo, hi = crf_range
    chosen = bisect_crf(check, lo, hi)
    achieved = next(p.vmaf for p in reversed(probes) if p.crf == chosen)
    return chosen, achieved, probes


def find_optimal_crf_and_skip(
    movie: Path,
    workdir: Path,
    handbrake: str,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    encoder: str = "x265",
    encoder_preset: str = "medium",
    crop: tuple[int, int, int, int] | None = None,
) -> tuple[int, float, list[SampleProbe], bool]:
    """Finds the optimal CRF and predicts whether the encode is worthwhile.

    Before the quality bisect, a single size probe estimates whether the
    re-encode can shrink the file at all (e.g. an already-efficient HEVC
    source may grow). For such titles the expensive search is skipped.

    Args:
        movie (Path): The source movie file.
        workdir (Path): Working directory for samples and probes.
        handbrake (str): Path to the HandBrakeCLI binary.
        target_vmaf (float): The VMAF quality floor to meet.
        crf_range (tuple[int, int]): The (lowest, highest) CRF to search.
        sample_count (int): The number of samples to probe.
        sample_seconds (float): The length of each sample in seconds.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_preset (str): The encoder speed preset, e.g. 'medium'.
        crop (tuple | None): Crop bars (top, bottom, left, right) applied to
            probes and the decision, matching the final encode.

    Returns:
        tuple: The chosen CRF, the VMAF at that CRF, all probes and a flag
            that is True when the movie should NOT be re-encoded.
    """
    samples = [s.resolve() for s in extract_samples(movie, workdir, sample_count, sample_seconds)]

    # cheapest CRF (largest size) of the search range as the pessimistic bound
    factor = estimate_size_factor(
        samples, workdir, handbrake, crf_range[1], encoder, encoder_preset, crop=crop
    )
    if factor >= 1.0:
        log(
            f"Skipping '{movie.name}': re-encode would not shrink it "
            f"(probe factor {factor:.2f} at CRF {crf_range[1]})."
        )
        return crf_range[1], 0.0, [], True

    chosen, achieved, probes = find_optimal_crf(
        movie,
        workdir,
        handbrake,
        target_vmaf=target_vmaf,
        crf_range=crf_range,
        sample_count=sample_count,
        sample_seconds=sample_seconds,
        encoder=encoder,
        encoder_preset=encoder_preset,
        crop=crop,
    )
    return chosen, achieved, probes, False


def encode_with_quality(
    input_file: Path,
    output_file: Path,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    encoder: str = "x265",
    encoder_preset: str = "medium",
    remove_bars: bool = False,
) -> QualityResult:
    """Re-encodes a movie with the largest CRF that meets the VMAF target.

    Short samples of the movie are probed first (bisect search), then the
    whole movie is encoded once with the winning CRF. With remove_bars,
    stable baked-in black bars (letterbox) are detected and cropped away.

    Args:
        input_file (Path): The file to re-encode.
        output_file (Path): The target file (must not exist).
        target_vmaf (float): The VMAF quality floor to meet.
        crf_range (tuple[int, int]): The (lowest, highest) CRF to search.
        sample_count (int): The number of samples to probe.
        sample_seconds (float): The length of each sample in seconds.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_preset (str): The encoder speed preset, e.g. 'medium'.
        remove_bars (bool): Detect and crop stable black bars (opt-in, the
            crop is irreversible).

    Returns:
        QualityResult: The result of the encode.

    Raises:
        FileNotFoundError: If the input file does not exist.
        RuntimeError: If HandBrakeCLI is not installed.
    """
    if not input_file.is_file():
        raise FileNotFoundError(f"Input file '{input_file}' does not exist!")

    duration = probe_duration_seconds(input_file)
    if duration is not None and duration < MIN_ENCODE_SECONDS:
        return QualityResult(
            input=input_file,
            output=output_file,
            success=False,
            target_vmaf=target_vmaf,
            error=(
                f"skipped: duration {duration:.0f}s is below the "
                f"{MIN_ENCODE_SECONDS:.0f}s minimum (not a movie?)"
            ),
        )

    handbrake = find_handbrake()
    if handbrake is None:
        raise RuntimeError(
            f"'{HANDBRAKE_BINARY}' not found! Install HandBrake CLI "
            "(https://handbrake.fr/docs/en/latest/cli/cli-options.html)"
        )

    workdir = Path(tempfile.mkdtemp(prefix="metadata_quality_"))
    try:
        crop = detect_crop_rect(input_file, workdir) if remove_bars else None
        if crop is not None:
            log(
                f"Removing stable black bars of '{input_file.name}': "
                f"top {crop[0]}, bottom {crop[1]}, left {crop[2]}, right {crop[3]}"
            )
        log(f"Probing '{input_file.name}' (target VMAF {target_vmaf})...")
        crf, achieved, probes, skip = find_optimal_crf_and_skip(
            input_file,
            workdir,
            handbrake,
            target_vmaf=target_vmaf,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            encoder=encoder,
            encoder_preset=encoder_preset,
            crop=crop,
        )
        if skip:
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probes=probes,
                error="skipped: re-encode would not shrink the file",
            )
        log(f"Chosen CRF {crf} (VMAF {achieved})")

        command = build_encode_command(
            handbrake, input_file, output_file, crf, encoder, encoder_preset, crop=crop
        )
        code, err = _run(command)
        if code != 0:
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probes=probes,
                error=err or "handbrake failed",
            )

        # HandBrake may exit with 0 even if the encode failed => the output
        # file must exist, be non-empty and have the full duration
        if not output_file.is_file() or output_file.stat().st_size == 0:
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probes=probes,
                error=err or "no output file was written",
            )
        if not durations_match(input_file, output_file):
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probes=probes,
                error="output is truncated (duration mismatch)",
            )

        return QualityResult(
            input=input_file,
            output=output_file,
            success=True,
            target_vmaf=target_vmaf,
            chosen_crf=crf,
            achieved_vmaf=achieved,
            probes=probes,
            input_size=input_file.stat().st_size,
            output_size=output_file.stat().st_size,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
