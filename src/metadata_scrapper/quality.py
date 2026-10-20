"""Perceptual quality optimization for re-encoding (VMAF-driven CRF search).

Implements the 'per-title' idea popularized by Netflix: instead of a fixed
quality setting, short samples of the movie are probed, their perceptual
quality is measured with VMAF (Netflix's video quality metric, computed by
ffmpeg's libvmaf filter) and the largest HandBrake CRF that still meets the
target quality is chosen => lowest file size without visible quality loss.

The CRF search is a binary search ('bisect'): VMAF falls monotonically as
CRF rises, so ~5 probe encodes are enough to find the quality knee.
"""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from metadata_scrapper.encode import HANDBRAKE_BINARY, find_handbrake
from metadata_scrapper.logger import log

FFMPEG_BINARY = "ffmpeg"

# Netflix's reference model (https://github.com/Netflix/vmaf), shipped with the
# package because old ffmpeg/libvmaf builds cannot load embedded models.
VMAF_MODEL = Path(__file__).parent / "models" / "vmaf_v0.6.1.json"

# VMAF 95 is Netflix's 'visually transparent' zone (93 is their common
# top-tier floor); VMAF falls monotonically with rising CRF.
DEFAULT_TARGET_VMAF = 95.0
# CRF 14 is near-transparent for x265, 32 is clearly lossy for live action.
DEFAULT_CRF_RANGE = (14, 32)
DEFAULT_SAMPLE_COUNT = 3
DEFAULT_SAMPLE_SECONDS = 10.0


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

    Returns:
        list[str]: The command as argument list.
    """
    return [
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
        # keep all audio and subtitle tracks, copy audio => no quality loss
        "--all-audio",
        "--all-subtitles",
        "--aencoder",
        "copy",
        "--optimize",  # web optimized => mp4/mkv file can start streaming
    ]


def build_vmaf_command(distorted: Path, reference: Path) -> list[str]:
    """Builds the ffmpeg command measuring the VMAF of an encode.

    The model file is copied to the working directory by measure_vmaf and
    referenced by bare filename => no Windows path escaping in the
    filtergraph (colons in drive letters break filter parsing).

    Args:
        distorted (Path): The encoded file (libvmaf 'main' input).
        reference (Path): The pristine source file (libvmaf 'reference').

    Returns:
        list[str]: The command as argument list (run with cwd=workdir).
    """
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
        "[0:v][1:v]libvmaf=log_path=vmaf.json:log_fmt=json:model_path=vmaf_v0.6.1.json:n_threads=0",
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
    first input, otherwise scores are inflated.

    Args:
        distorted (Path): The encoded file.
        reference (Path): The pristine source file.
        workdir (Path): Working directory for the model and log files.

    Returns:
        float: The mean VMAF score.

    Raises:
        RuntimeError: If ffmpeg/libvmaf failed or produced no score.
    """
    model = workdir / VMAF_MODEL.name
    shutil.copyfile(VMAF_MODEL, model)

    code, err = _run(build_vmaf_command(distorted, reference), cwd=workdir)
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

    Returns:
        float: The lowest per-sample VMAF mean at this CRF.

    Raises:
        RuntimeError: If a sample encode or VMAF measurement failed.
    """
    scores: list[float] = []
    for index, sample in enumerate(samples):
        encoded = workdir / f"probe_crf{crf}_{index}.mkv"
        command = build_encode_command(handbrake, sample, encoded, crf, encoder, encoder_preset)
        code, err = _run(command)
        if code != 0 or not encoded.is_file() or encoded.stat().st_size == 0:
            raise RuntimeError(f"probe encode failed (crf {crf}): {err[-400:]}")
        scores.append(measure_vmaf(encoded, sample, workdir))
        encoded.unlink()
    return min(scores)


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

    Returns:
        tuple: The chosen CRF, the VMAF measured at that CRF and all probes.

    Raises:
        RuntimeError: If sampling or probing failed.
    """
    samples = extract_samples(movie, workdir, sample_count, sample_seconds)

    probes: list[SampleProbe] = []

    def check(crf: int) -> bool:
        vmaf = probe_crf(samples, crf, workdir, handbrake, encoder, encoder_preset)
        probes.append(SampleProbe(crf=crf, vmaf=round(vmaf, 2)))
        return vmaf >= target_vmaf

    lo, hi = crf_range
    chosen = bisect_crf(check, lo, hi)
    achieved = next(p.vmaf for p in reversed(probes) if p.crf == chosen)
    return chosen, achieved, probes


def encode_with_quality(
    input_file: Path,
    output_file: Path,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int = DEFAULT_SAMPLE_COUNT,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    encoder: str = "x265",
    encoder_preset: str = "medium",
) -> QualityResult:
    """Re-encodes a movie with the largest CRF that meets the VMAF target.

    Short samples of the movie are probed first (bisect search), then the
    whole movie is encoded once with the winning CRF.

    Args:
        input_file (Path): The file to re-encode.
        output_file (Path): The target file (must not exist).
        target_vmaf (float): The VMAF quality floor to meet.
        crf_range (tuple[int, int]): The (lowest, highest) CRF to search.
        sample_count (int): The number of samples to probe.
        sample_seconds (float): The length of each sample in seconds.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_preset (str): The encoder speed preset, e.g. 'medium'.

    Returns:
        QualityResult: The result of the encode.

    Raises:
        FileNotFoundError: If the input file does not exist.
        RuntimeError: If HandBrakeCLI is not installed.
    """
    if not input_file.is_file():
        raise FileNotFoundError(f"Input file '{input_file}' does not exist!")

    handbrake = find_handbrake()
    if handbrake is None:
        raise RuntimeError(
            f"'{HANDBRAKE_BINARY}' not found! Install HandBrake CLI "
            "(https://handbrake.fr/docs/en/latest/cli/cli-options.html)"
        )

    workdir = Path(tempfile.mkdtemp(prefix="metadata_quality_"))
    try:
        log(f"Probing '{input_file.name}' (target VMAF {target_vmaf})...")
        crf, achieved, probes = find_optimal_crf(
            input_file,
            workdir,
            handbrake,
            target_vmaf=target_vmaf,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            encoder=encoder,
            encoder_preset=encoder_preset,
        )
        log(f"Chosen CRF {crf} (VMAF {achieved})")

        command = build_encode_command(
            handbrake, input_file, output_file, crf, encoder, encoder_preset
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
        # file must exist and be non-empty
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
