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

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

from shrinkray import resources
from shrinkray.encode import (
    HANDBRAKE_BINARY,
    MIN_ENCODE_SECONDS,
    _encode_with_command,
    find_handbrake,
    probe_duration_seconds,
    stall_timeout_seconds,
)
from shrinkray.logger import log, log_error, log_warning
from shrinkray.media import FFPROBE_TIMEOUT
from shrinkray.telemetry import save_failure

FFMPEG_BINARY = "ffmpeg"

# the decision statistic (issue #15): the mean of the per-sample
# VMAF means, not the minimum. A minimum is monotone non-increasing
# in the sample count, which punishes denser sampling (#14).
VMAF_STATISTIC = "mean"

# cropdetect windows per movie: with the bars common to every sample the
# crop is monotone non-increasing in the sample count, so more samples only
# ever shrink it. That is the safe direction for an irreversible crop, and it
# makes an aspect-ratio switch much less likely to be missed (issue #17).
DEFAULT_CROP_SAMPLE_COUNT = 10

# PRODUCT.md's source-codec rule: an H.264 source is a candidate on size
# alone, while a modern codec (HEVC/AV1) is already efficient and is only
# worth re-encoding when it has bars to remove. Anything older is never
# re-encoded. The names are ffprobe's own codec_name values.
CANDIDATE_CODECS = ("h264",)
MODERN_CODECS = ("hevc", "av1")
MIN_SOURCE_CODEC = "h264"

# the engine's live-progress callback: stage detail and counters, never persisted
ProgressCallback = Callable[[dict], None]

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
            timeout=FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(
            f"ffmpeg ('{binary}') not found or did not answer within "
            f"{FFPROBE_TIMEOUT}s! Install a build with libvmaf support. ({e})"
        ) from e
    options = proc.stdout.decode(errors="replace")
    if "libvmaf" not in options and "libvmaf" not in proc.stderr.decode(errors="replace"):
        raise RuntimeError("ffmpeg has no libvmaf filter! Install a build with libvmaf support.")
    _vmaf_style = VMAF_STYLE_MODERN if "model_path" not in options else VMAF_STYLE_LEGACY
    return _vmaf_style


# VMAF 95 is Netflix's 'visually transparent' zone (93 is their common
# top-tier floor); VMAF falls monotonically with rising CRF.
DEFAULT_TARGET_VMAF = 95.0
# AV1 is the only output codec (issue #21): SVT-AV1 on the CPU, 10-bit for
# HDR. x265 is gone from every path - not demoted to a fallback, not kept
# as an option. encoder_for() is the single source of this choice.
DEFAULT_ENCODER = "svt_av1"
HDR_ENCODER = "svt_av1_10bit"
# HDR measurement runs on tone-mapped SDR twins, whose scale sits slightly
# below the SDR-native one (a Mandalorian sample: CRF 14 = 94.78, CRF 10 =
# 96.49); 94.5 is the calibrated equivalent of the 95 transparency floor
DEFAULT_HDR_TARGET_VMAF = 94.5
# SVT-AV1 publishes 0-70 at 0.25 granularity (HandBrake's
# hb_video_quality_get_limits), a wider and finer scale than x265's.
# These bounds are NOT calibrated for SVT-AV1 yet: they are the
# x265-era ranges carried over, and the issue requires a measured
# SVT-AV1 CRF-to-VMAF curve on 3-5 titles before they can be
# trusted. The search clamps to this band, so an over-wide range only
# costs probes - it cannot make the decision wrong in the safe direction.
DEFAULT_CRF_RANGE = (14, 45)
DEFAULT_HDR_CRF_RANGE = (10, 45)
DEFAULT_SAMPLE_COUNT = 3
DEFAULT_SAMPLE_SECONDS = 10.0
# probe density as a duration: one window per N seconds of runtime, so
# coverage is a fixed fraction (sample_seconds / sample_every) instead of
# shrinking as the movie gets longer. None keeps the count-based default.
DEFAULT_SAMPLE_EVERY = 600.0

# libvmaf v3 thread cap: values above ~8 crash the filter with EINVAL on
# some builds (frame queue limit), so stay on the safe side
MAX_VMAF_THREADS = 8

# full-file verification sub-samples above this output size (n_subsample=10:
# every 10th frame measured) - full decode of a 2-hour 4K movie takes hours,
# a sub-sampled pass minutes, the score stays honest
VERIFY_SUBSAMPLE_THRESHOLD_BYTES = 2 * 1024**3

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
    """Intersects picture rectangles => only bars present in ALL samples.

    cropdetect reports the *picture* rectangle of each sample, so keeping the
    picture area common to every sample means cropping only the bars that every
    sample agrees on. A sample without bars keeps the full frame, which vetoes
    the crop entirely - no picture content can be removed.

    Args:
        rects (list): Detected (width, height, x, y) picture rectangles.
        frame (tuple): The (width, height) of the source frame.

    Returns:
        tuple | None: The (top, bottom, left, right) bars to crop, or None
            if no stable bars were detected or they are too small to matter.
    """
    if not rects:
        return None
    frame_w, frame_h = frame

    # union of the picture areas = the bars common to every sample
    left = min(x for _, _, x, _ in rects)
    top = min(y for _, _, _, y in rects)
    right = max(x + w for w, _, x, _ in rects)
    bottom = max(y + h for _, h, _, y in rects)

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
    movie: Path, workdir: Path, count: int = DEFAULT_CROP_SAMPLE_COUNT
) -> tuple[int, int, int, int] | None:
    """Detects baked-in black bars (letterbox) of a movie via cropdetect.

    Runs cropdetect on several segments and keeps the bars common to all of
    them, so only bars that are present throughout the movie are removed.

    Args:
        movie (Path): The source movie file.
        workdir (Path): Working directory (unused, kept for symmetry).
        count (int): The number of segments to probe.

    Returns:
        tuple | None: The (top, bottom, left, right) bars to crop, or None
            if no stable bars were detected.
    """
    from shrinkray.encode import probe_duration_seconds

    duration = probe_duration_seconds(movie)
    if duration is None:
        return None

    try:
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
            timeout=FFPROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as e:
        # OSError: ffprobe missing. TimeoutExpired: stale mount blocking the
        # kernel read — without a timeout the watch loop/job worker stalls
        # forever with no error (issue #62). No frame size => no crop.
        log(f"ffprobe could not read '{movie}' within {FFPROBE_TIMEOUT}s: {e}")
        return None
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
        target_vmaf (float): The effective VMAF quality floor that was met:
            the requested one, or the HDR override when the movie is HDR.
        chosen_crf (int | None): The CRF selected by the bisect.
        achieved_vmaf (float | None): The mean per-sample VMAF measured at
            the chosen CRF (None if no probe was possible). Not the worst
            window: a mean is robust against a single pathological sample,
            which is why the decision statistic is the mean (issue #15).
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
    skipped_reason: str | None = None
    encoder_preset: str | None = None
    probe_encoder_preset: str | None = None
    encode_seconds: float | None = None
    probe_seconds: float | None = None
    verified_vmaf: float | None = None
    verified_subsample: int | None = None
    hdr: bool = False
    tool_versions: str | None = None
    input_width: int | None = None
    input_height: int | None = None
    input_codec: str | None = None
    input_duration: float | None = None
    input_bitrate: float | None = None
    output_bitrate: float | None = None
    # the probing pipeline's effective decision inputs (fingerprint of a
    # stored plan): HDR overrides target/range before probing, so these
    # carry the OVERRIDDEN values
    crop: tuple[int, int, int, int] | None = None
    remove_bars: bool = False
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE
    sample_count: int = DEFAULT_SAMPLE_COUNT
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS
    crop_sample_count: int = DEFAULT_CROP_SAMPLE_COUNT
    # which aggregation produced achieved_vmaf: "mean" is the decision
    # statistic (issue #15), "min" marks a plan measured before it
    vmaf_statistic: str = "mean"
    # provenance of the CRF decision ('plan' when reused from a stored
    # plan, 'probe' when measured now, None when not applicable)
    crf_source: str | None = None
    plan_created_at: str | None = None
    # the encoder the decision was made against: the requested one, or the
    # HDR override ('x265_10bit') — one source of truth for the record and
    # the plan cache (a plan of an HDR movie is only valid under 10-bit)
    encoder: str = DEFAULT_ENCODER

    @property
    def saved_percent(self) -> float:
        """Percentage of disk space saved by the encode."""
        if self.input_size <= 0 or self.output_size <= 0:
            return 0.0
        return round((1 - self.output_size / self.input_size) * 100, 2)


def _run(
    command: list[str],
    cwd: Path | None = None,
    cancel: threading.Event | None = None,
) -> tuple[int, str]:
    """Runs a subprocess and returns (returncode, stderr as text).

    With a cancel event, the process is polled and terminated when the
    event fires; the returncode -1 signals the cancellation to the caller.
    Both pipes are drained by daemon threads while the process runs: a
    child writing more than the ~64K pipe buffer (HandBrake progress on
    stdout, libvmaf/ffmpeg warnings on stderr) would otherwise block
    forever while this loop only polls for exit — a silent deadlock.

    Args:
        command (list[str]): The command to run.
        cwd (Path | None): Working directory.
        cancel (threading.Event | None): Cooperative cancellation.

    Returns:
        tuple[int, str]: The returncode (-1 when cancelled) and stderr text.
    """
    if cancel is None and stall_timeout_seconds() is None:
        # Neither cancellation nor stall watching is possible: the simple
        # blocking run (used when both protections are explicitly off).
        # Fallback decision (issue #62): no timeout here on purpose — the
        # probes this runs are minutes-long encodes, a fixed timeout would
        # kill healthy work, and the operator has explicitly disabled stall
        # protection. "Watchdog off" means no protection; the streaming
        # Popen path below is the protected shape.
        proc = subprocess.run(command, capture_output=True, cwd=cwd, **resources.spawn_kwargs())
        return proc.returncode, proc.stderr.decode(errors="replace").strip()
    popen = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        **resources.session_kwargs(),
        **resources.spawn_kwargs(),
    )
    assert popen.stdout is not None and popen.stderr is not None
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    last_output_cell = [time.monotonic()]  # drain threads report progress

    def _drain(pipe: "IO[bytes]", chunks: list[bytes]) -> None:
        """Reads a pipe to its EOF into chunks (runs in a daemon thread).

        Every chunk read also refreshes the shared last-output timestamp:
        the stall watchdog in the poll loop cannot see the pipes itself,
        and a healthy child must never be mistaken for a hung one.
        """
        try:
            for chunk in iter(lambda: pipe.read(4096), b""):
                chunks.append(chunk)
                last_output_cell[0] = time.monotonic()
        except (ValueError, OSError):
            pass  # pipe closed during shutdown (cancellation race)

    drainers = [
        threading.Thread(target=_drain, args=(popen.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_drain, args=(popen.stderr, stderr_chunks), daemon=True),
    ]
    for drainer in drainers:
        drainer.start()
    cancelled = False
    stalled = False
    stall_seconds = stall_timeout_seconds()
    try:
        while popen.poll() is None:
            if cancel is not None and cancel.is_set():
                resources.terminate_tree(popen)
                cancelled = True
                break
            # stall watchdog: probes/measurements must make progress too
            # (ffmpeg can hang on odd streams); same semantics as the final
            # encode - terminate the hung child, report it as a failure
            if stall_seconds is not None and time.monotonic() - last_output_cell[0] > stall_seconds:
                log_error(
                    f"Stall watchdog: no output from the probe/measure child for "
                    f"{stall_seconds / 60:.0f} min => terminating it (hung child)."
                )
                resources.terminate_tree(popen)
                cancelled = False
                stalled = True
                break
            time.sleep(0.2)
        for drainer in drainers:
            drainer.join(timeout=5)
        if cancelled:
            return -1, "cancelled by user"
        if stalled:
            assert stall_seconds is not None  # stalled=True implies it was set
            return -2, (f"stalled: no output for {stall_seconds / 60:.0f} min (terminated)")
        stderr = b"".join(stderr_chunks).decode(errors="replace").strip()
        return popen.returncode, stderr
    finally:
        # close OUR read ends first: a drainer blocked in read() (orphan
        # still holding the write end) must not deadlock close() itself -
        # closing breaks the read, the bounded join above cleans up
        if popen.stdout:
            popen.stdout.close()
        if popen.stderr:
            popen.stderr.close()


# the sample extraction recipe, part of the probe cache key: a change to
# how samples are extracted changes their encoded bytes (and thus every
# probe's VMAF), so cached scores must not survive it (see #16)
SAMPLE_EXTRACTION_TAG = "x264-crf12-veryfast"


def probe_cache_key(
    movie: Path,
    offset: float,
    seconds: float,
    crf: int,
    encoder: str,
    encoder_preset: str,
    crop: tuple[int, int, int, int] | None,
    hdr: bool,
    encoder_threads: int | None,
    tool_versions: str,
    kind: str = "vmaf",
) -> str:
    """Computes the content-addressed key of one probe measurement.

    Every input that can change the probe's encoded bytes or its VMAF
    score belongs in the key: the sample's content identity (source size
    + mtime, window offset/length, extraction recipe), the encode
    (encoder, preset, CRF, crop rect, thread cap) and the measurement
    (HDR tone-mapping, tool versions). The source PATH is deliberately
    not part of the key: a moved-but-unmodified file's probes stay valid
    (a move preserves mtime and size).

    Args:
        movie (Path): The source movie file (identity via size + mtime).
        offset (float): The sample window's start offset in seconds.
        seconds (float): The sample window's length in seconds.
        crf (int): The probed CRF.
        encoder (str): The effective video encoder.
        encoder_preset (str): The PROBE's speed preset (a measurement
            setting, issue #22).
        hdr (bool): Whether the VMAF measurement tone-maps both sides.
        encoder_threads (int | None): The thread cap of the probe encode.
        tool_versions (str): The tool version string (HandBrake, libvmaf).
        kind (str): 'vmaf' (a VMAF score) or 'size' (an encoded-size ratio).

    Returns:
        str: The sha256 hex digest of the joined key inputs.
    """
    stat = movie.stat()
    parts = [
        kind,
        str(stat.st_size),
        f"{stat.st_mtime:.6f}",
        f"{offset:.3f}",
        f"{seconds:.3f}",
        SAMPLE_EXTRACTION_TAG,
        crf,
        encoder,
        encoder_preset,
        "none" if crop is None else ",".join(str(v) for v in crop),
        "hdr" if hdr else "sdr",
        "threads:" + ("all" if encoder_threads is None else str(encoder_threads)),
        tool_versions,
    ]
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()


def _cached_or_none(
    db_file: Path | None, key: str
) -> tuple[float | None, sqlite3.Connection | None]:
    """Reads a probe-cache entry, returning the connection to keep it open on.

    Opening one short-lived connection per probe is cheap (WAL, single
    statements) and keeps probe_crf/estimate_size_factor connection-free
    like the rest of the engine. Returns (None, None) without a db_file:
    probing then runs uncached (CLI one-shots without a database).

    Args:
        db_file (Path | None): The database file (None => no caching).
        key (str): The probe cache key.

    Returns:
        tuple: The cached value (None on miss) and the open connection
            to hand to the cache write, or (None, None).
    """
    if db_file is None:
        return None, None
    from shrinkray import db as _db

    connection = _db.connect(db_file)
    return _db.get_probe_score(connection, key), connection


def _store_probe(connection: sqlite3.Connection | None, key: str, value: float) -> None:
    """Stores one probe measurement (best-effort commit via db layer)."""
    if connection is None:
        return
    from shrinkray import db as _db

    _db.save_probe_score(connection, key, value)
    connection.close()


def sample_count_for(
    duration: float | None,
    sample_every: float | None,
    sample_count: int | None = None,
) -> int:
    """Derives the probe window count from the runtime and the density.

    A count-based knob makes coverage inverse to runtime: the longer the movie,
    the more scenes it has and the less of it gets probed. Deriving the count
    from `sample_every` keeps the probed fraction fixed at
    `sample_seconds / sample_every` regardless of length, which is the
    "consistent experience across the file" property worth having.

    An explicit `sample_count` wins when set, so a latency-sensitive caller
    (watch mode) can still pin a fixed probe budget.

    Args:
        duration (float | None): The movie's runtime in seconds.
        sample_every (float | None): Seconds of runtime per probe window
            (None or 0 disables the density knob).
        sample_count (int | None): An explicit count that overrides the density.

    Returns:
        int: The number of probe windows (at least 1).
    """
    if sample_count is not None and sample_count > 0:
        return sample_count
    if duration is None or duration <= 0 or not sample_every or sample_every <= 0:
        return DEFAULT_SAMPLE_COUNT
    return max(1, math.ceil(duration / sample_every))


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


def extract_samples(
    movie: Path,
    workdir: Path,
    count: int,
    seconds: float,
    cancel: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> list[tuple[Path, float]]:
    """Extracts sample clips for probing by stream-copying the source.

    The samples are the VMAF *reference* and the probe encode's input, so a
    re-encode here would bias both sides of every measurement: a lossy pass
    smooths grain and fine detail, making the probe source easier than the real
    one and the chosen CRF too high. A stream copy keeps the reference
    pristine, and it is I/O-bound rather than CPU-bound.

    `-ss` before `-i` with `-c:v copy` starts the clip at the keyframe at or
    before the offset, so the clip is slightly longer than `seconds`. Both the
    probe encode and the VMAF reference read the same file, so the comparison
    stays frame-aligned; `-avoid_negative_ts make_zero` normalises the
    timestamps open-GOP sources otherwise carry into the clip.

    If the container cannot hold the source codec the copy fails and the clip is
    re-extracted losslessly (ffv1), which keeps the reference pristine but
    costs a decode + encode; that fallback is logged so the operator knows the
    extraction was expensive.

    Args:
        movie (Path): The source movie file.
        workdir (Path): Directory for the sample files.
        count (int): The number of samples.
        seconds (float): The length of each sample in seconds.
        cancel (threading.Event | None): Cooperative cancellation.
        progress (callable | None): Callback progress(dict) for live
            step updates (flow to the UI, never persisted).

    Returns:
        list[tuple[Path, float]]: The sample files with their start offsets
            in seconds (the offset is part of a probe's cache identity).

    Raises:
        RuntimeError: If a sample could not be extracted.
        Cancelled: When the cancel event fires.
    """
    _check_cancel(cancel)
    try:
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
            timeout=FFPROBE_TIMEOUT,
        )
    except subprocess.SubprocessError as e:
        # TimeoutExpired: a stale mount (NFS/SMB that went away) blocks the
        # read in the kernel — without a timeout this call would hang the
        # job worker/watch loop forever with no error (issue #62).
        raise RuntimeError(f"ffprobe timed out after {FFPROBE_TIMEOUT}s for '{movie}': {e}") from e
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for '{movie}'")
    duration = float(json.loads(proc.stdout.decode())["format"]["duration"])

    samples: list[tuple[Path, float]] = []
    for index, offset in enumerate(sample_offsets(duration, count)):
        _check_cancel(cancel)
        if progress:
            progress(
                {
                    "stage": "probe",
                    "detail": f"extracting sample {index + 1}/{count}",
                    "done": index,
                    "total": count,
                }
            )
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
                "copy",
                "-avoid_negative_ts",
                "make_zero",
                "-an",
                str(sample),
            ],
            cancel=cancel,
        )
        if code != 0 or not sample.is_file() or sample.stat().st_size == 0:
            # the container cannot hold the source codec: re-extract losslessly
            # so the reference stays pristine, and say so - this path costs a
            # full decode and encode instead of a copy
            log_warning(
                f"Sample extraction could not stream-copy '{movie.name}' "
                f"at {offset:.0f}s; re-extracting losslessly (slower)."
            )
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
                    "ffv1",
                    "-avoid_negative_ts",
                    "make_zero",
                    "-an",
                    str(sample),
                ],
                cancel=cancel,
            )
        if code != 0 or not sample.is_file() or sample.stat().st_size == 0:
            raise RuntimeError(f"sample extraction failed at {offset:.0f}s: {err[-400:]}")
        samples.append((sample, offset))
    return samples


def build_encode_command(
    binary: str,
    input_file: Path,
    output_file: Path,
    crf: int,
    encoder: str,
    encoder_preset: str,
    crop: tuple[int, int, int, int] | None = None,
    encoder_threads: int | None = None,
) -> list[str]:
    """Builds the HandBrakeCLI command for a quality-driven encode.

    Unlike the fixed preset in encode.py, the quality is set via --quality
    (CRF) and the speed via --encoder-preset.

    Args:
        binary (str): Path to the HandBrakeCLI binary.
        input_file (Path): The file to re-encode.
        output_file (Path): The target file.
        crf (int): The CRF quality value.
        encoder (str): The video encoder, e.g. 'svt_av1'.
        encoder_preset (str): The encoder speed preset to encode at.
        crop (tuple | None): Crop bars (top, bottom, left, right) to remove
            baked-in black bars. None keeps the source resolution.
        encoder_threads (int | None): Thread limit for the encoder
            (None = no limit). Keeps cores free for other work.

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
    if encoder_threads is not None:
        # HandBrakeCLI has no thread flag, and SVT-AV1 has no `threads`
        # option: --encopts pairs go to svt_av1_enc_parse_parameter, which
        # would silently ignore `threads=N` (issue #21). SVT-AV1's `pin`
        # restricts execution to the first N cores, which is what the cap
        # means here.
        command += ["--encopts", f"pin={encoder_threads}"]
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
    distorted: Path,
    reference: Path,
    threads: int = 0,
    style: str | None = None,
    crop: tuple[int, int, int, int] | None = None,
    hdr: bool = False,
    subsample: int | None = None,
) -> list[str]:
    """Builds the ffmpeg command measuring the VMAF of an encode.

    The model file is copied to the working directory by measure_vmaf and
    referenced by bare filename => no Windows path escaping in the
    filtergraph (colons in drive letters break filter parsing). Works with
    both the legacy (<6) and modern libvmaf filter API.

    With a crop, the reference is cropped to the same geometry as the
    distorted input, otherwise libvmaf fails on the dimension/SAR mismatch
    (e.g. PAL DVD content with uneven side bars).

    With HDR, both inputs are tone-mapped to SDR inside the filtergraph
    before the comparison: the VMAF model is SDR-trained and PQ content
    would score misleadingly. Both sides get the identical chain, so the
    relative quality measurement stays honest.

    Args:
        distorted (Path): The encoded file (libvmaf 'main' input).
        reference (Path): The pristine source file (libvmaf 'reference').
        threads (int): libvmaf worker threads (0 = automatic). Meaningful
            only on modern libvmaf (v3+); legacy builds score single-threaded.
        style (str | None): Force a filter API style (legacy/modern); used by
            tests. None detects the local ffmpeg.
        crop (tuple | None): Crop bars (top, bottom, left, right) applied to
            the reference input to match the distorted geometry.
        hdr (bool): Tone-map both inputs to SDR (PQ/bt2020 sources).
        subsample (int | None): libvmaf n_subsample divisor: measure only
            every Nth frame (None/0/1 = every frame). Full-file verification
            of a 2-hour movie decodes EVERYTHING (hours); a sub-sampled pass
            (e.g. 10) cuts it to minutes while keeping the score honest
            (the sampled frames stay the SAME ones the probes never saw).

    Returns:
        list[str]: The command as argument list (run with cwd=workdir).
    """
    if style is None:
        style = detect_vmaf_style()
    if style == VMAF_STYLE_MODERN:
        model_option = "model=path=vmaf_v0.6.1.json"
    else:
        model_option = "model_path=vmaf_v0.6.1.json"

    vmaf = f"libvmaf=log_path=vmaf.json:log_fmt=json:{model_option}:n_threads={threads}"
    if subsample and subsample > 1:
        vmaf += f":n_subsample={int(subsample)}"

    # the tone-map chain: linear light -> bt709 primaries -> hable tonemap
    # -> back to video levels (validated on real HDR10 content)
    tonemap = (
        "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
        "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,"
        "format=yuv420p10le"
    )

    if hdr:
        # tone-map both sides to their SDR twins before comparing
        filtergraph = f"[0:v]{tonemap}[d];[1:v]{tonemap}[r];[d][r]{vmaf}"
    elif crop is not None:
        top, bottom, left, right = crop
        filtergraph = (
            f"[1:v]crop=iw-{left + right}:ih-{top + bottom}:{left}:{top}[r];[0:v][r]{vmaf}"
        )
    else:
        filtergraph = f"[0:v][1:v]{vmaf}"
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
        filtergraph,
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


def measure_vmaf(
    distorted: Path,
    reference: Path,
    workdir: Path,
    crop: tuple[int, int, int, int] | None = None,
    hdr: bool = False,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    subsample: int | None = None,
) -> float:
    """Measures the mean VMAF of an encode against its source.

    The main/reference input order matters: the distorted stream must be the
    first input, otherwise scores are inflated. Both input paths are resolved
    to absolute form because ffmpeg runs with cwd=workdir.

    Args:
        distorted (Path): The encoded file.
        reference (Path): The pristine source file.
        workdir (Path): Working directory for the model and log files.
        crop (tuple | None): Crop bars (top, bottom, left, right) applied to
            the reference so it matches the distorted geometry.
        hdr (bool): Tone-map both sides for the measurement.
        cancel (threading.Event | None): Cooperative cancellation.
        db_file (Path | None): The database file, used to locate the logs
            directory for failure evidence (None => cwd metadata.db).
        subsample (int | None): libvmaf n_subsample divisor (None = every
            frame). Used by the post-encode verification for large files.

    Returns:
        float: The mean VMAF score.

    Raises:
        RuntimeError: If ffmpeg/libvmaf failed or produced no score.
        Cancelled: When the cancel event fires.
    """
    distorted = distorted.resolve()
    reference = reference.resolve()
    model = workdir / VMAF_MODEL.name
    shutil.copyfile(VMAF_MODEL, model)

    threads = min(os.cpu_count() or MAX_VMAF_THREADS, MAX_VMAF_THREADS)
    if detect_vmaf_style() != VMAF_STYLE_MODERN:
        # legacy libvmaf (ffmpeg < 6) is single-threaded regardless of the option
        threads = 0
    command = build_vmaf_command(
        distorted, reference, threads, crop=crop, hdr=hdr, subsample=subsample
    )
    code, err = _run(
        command,
        cwd=workdir,
        cancel=cancel,
    )
    if code == -1:
        raise Cancelled("cancelled during vmaf measurement")
    log_file = workdir / "vmaf.json"
    if code != 0 or not log_file.is_file():
        # preserve the evidence (a failure with an EMPTY stderr is the
        # worst case: the bundle keeps stderr, command and any partial
        # vmaf.json alive for diagnosis)
        save_failure(
            db_file=db_file or Path("metadata.db"),
            kind="vmaf-measure",
            command=command,
            stderr=err,
            workdir=workdir,
            artifacts=["vmaf.json"],
            context={
                "distorted": str(distorted),
                "reference": str(reference),
                "hdr": hdr,
                "crop": crop,
                "subsample": subsample,
                "returncode": code,
            },
        )
        raise RuntimeError(f"libvmaf failed: {err[-400:]}")

    try:
        return parse_vmaf_log(log_file.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as e:
        raise RuntimeError(f"libvmaf log could not be parsed: {e}") from e


def source_codec(movie: Path) -> str | None:
    """Reads the source's video codec name for the codec gate.

    The value is ffprobe's own codec_name (h264, hevc, mpeg2video, vc1,
    av1, ...), so the gate compares against what ffprobe reports rather
    than a normalized spelling.

    Returns None when the codec cannot be determined, so the gate can treat
    "unknown" as "do not skip": an unreadable codec must never silently
    exclude a file the operator expected to be considered.

    Args:
        movie (Path): The movie file.

    Returns:
        str | None: The video stream's codec name, or None.
    """
    from shrinkray.config import load_config
    from shrinkray.media import get_data

    try:
        data = get_data(movie, load_config())
    except (RuntimeError, OSError, ValueError):
        return None
    codec = data.get("codec")
    if codec is None:
        return None
    return str(codec)


def source_height(movie: Path) -> int | None:
    """Reads the source video height for the resolution gate.

    Returns None when the height cannot be determined, so the caller can treat
    "unknown" as "do not skip": an unreadable height must never silently
    exclude a file the operator expected to be considered.

    Args:
        movie (Path): The movie file.

    Returns:
        int | None: The video stream's height in pixels, or None.
    """
    from shrinkray.config import load_config
    from shrinkray.media import get_data

    try:
        data = get_data(movie, load_config())
    except (RuntimeError, OSError, ValueError):
        return None
    dimensions = data.get("dimensions") or {}
    height = dimensions.get("height")
    if height is None:
        return None
    return int(height)


def encoder_for(hdr: bool) -> str:
    """Returns the only output encoder this product emits.

    PRODUCT.md makes AV1 the single output codec, encoded on the CPU: SDR
    encodes with ``svt_av1`` and HDR encodes with ``svt_av1_10bit``. H.264
    and H.265 are not demoted to fallbacks - they are gone. Keeping the
    choice in one function is what stops the six HDR ternaries that used to
    be written out per call site from drifting apart again.

    Args:
        hdr (bool): True when the source is HDR.

    Returns:
        str: The HandBrake encoder name.
    """
    return HDR_ENCODER if hdr else DEFAULT_ENCODER


def is_hdr_file(movie: Path) -> bool:
    """Checks whether a movie file is HDR.

    Reuses the scan's detection (media.get_data computes the hdr flag from
    the configured codec.hdr markers) so both paths always agree.

    Args:
        movie (Path): The movie file.

    Returns:
        bool: True if the video stream is detected as HDR.
    """
    from shrinkray.config import load_config
    from shrinkray.media import get_data

    try:
        data = get_data(movie, load_config())
    except (RuntimeError, OSError, ValueError):
        return False
    return bool(data.get("hdr"))


def _tool_versions(handbrake: str) -> str:
    """Reads the tool versions relevant for benchmark records.

    Args:
        handbrake (str): Path to the HandBrakeCLI binary.

    Returns:
        str: A compact version string, e.g. 'hb=1.11.2;libvmaf=v3'.
    """
    import re as _re
    import subprocess as _subprocess

    versions = []
    try:
        proc = _subprocess.run([handbrake, "--version"], capture_output=True, timeout=15)
        match = _re.search(r"HandBrake\s+([0-9.]+)", proc.stdout.decode(errors="replace"))
        if match:
            versions.append(f"hb={match.group(1)}")
    except (OSError, _subprocess.SubprocessError):
        pass
    try:
        style = detect_vmaf_style()
    except RuntimeError:
        # ffmpeg absent (CI runners, minimal NAS installs): version
        # metadata must never fail the encode result - the tool_versions
        # string is documentation, not function. Omit the libvmaf token.
        return ";".join(versions)
    versions.append("libvmaf=v3" if style == VMAF_STYLE_MODERN else "libvmaf=v2")
    return ";".join(versions)


def _source_snapshot(movie: Path) -> dict:
    """Reads the source file's resolution/codec/duration/bitrate for encode records.

    The encode record must be self-contained: the files table row can change
    on a later re-scan, so the benchmark data snapshots what was true at
    encode time. The bitrate feeds the generation-drift documentation
    (input_bitrate/output_bitrate on the encodes row).

    Args:
        movie (Path): The source file.

    Returns:
        dict: width/height/codec/duration/bitrate (None where unknown).
    """
    from shrinkray.encode import probe_duration_seconds

    duration = probe_duration_seconds(movie)
    width = height = None
    codec = None
    bitrate = None
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                str(movie),
            ],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout.decode())
            streams = data.get("streams", [])
            video: dict = next((s for s in streams if s.get("codec_type") == "video"), {})
            width = video.get("width")
            height = video.get("height")
            codec = video.get("codec_name")
            # the file bitrate (video+audio+containers); the video stream's
            # own bit_rate is often absent => fall back to the format level
            raw = video.get("bit_rate") or data.get("format", {}).get("bit_rate")
            if raw:
                bitrate = round(float(raw) / 1_000_000, 2)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {
        "width": width,
        "height": height,
        "codec": codec,
        "duration": duration,
        "bitrate": bitrate,
    }


def _output_bitrate(output_file: Path, duration: float | None) -> float | None:
    """Computes the output bitrate in MBit/s from size and duration.

    Size/duration is the honest number for the drift chain (a ffprobe of
    the output would re-read the container and return the same sum).

    Args:
        output_file (Path): The encoded file.
        duration (float | None): The duration in seconds (shared by both
            sides — the truncation check above verified equality).

    Returns:
        float | None: Bitrate in MBit/s, None when unknowable.
    """
    if not duration or duration <= 0 or not output_file.is_file():
        return None
    return round(output_file.stat().st_size * 8 / duration / 1_000_000, 2)


def bisect_crf(score: Callable[[int], float], lo: int, hi: int, target_vmaf: float) -> int:
    """Finds the largest CRF whose probe still meets the quality target.

    The search is a bracketing interpolation (issue #23, the shape ab-av1's
    crf_search uses): each round measures a CRF between the current passing
    and failing brackets, guessed by linear interpolation in VMAF space
    (a typical VMAF curve reaches the target in fewer probes than a plain
    midpoint bisect). Two safeguards keep the worst case at the bisect's:

    - the guess is clamped strictly inside the bracket (interpolation can
      point outside on near-flat curves),
    - the guess must shrink the bracket by at least half — a strongly
      convex/step-like curve would make regula falsi crawl, so the search
      falls back to the midpoint when interpolation would not.

    Args:
        score (callable): Measurement score(crf) => float, the probe's VMAF
            at that CRF. Must be monotonic: high at low CRF, low at high
            CRF. Every CRF is measured at most once (cacheable inside the
            callable).
        lo (int): The lowest CRF to consider. Failing here is the "target
            unreachable" signal (callers skip the movie on it).
        hi (int): The highest CRF to consider (exclusive, never probed).

    Returns:
        int: The largest passing CRF in [lo, hi), or lo if even lo fails
            (the caller should use it anyway as the best available quality).
    """
    vmaf_lo = score(lo)
    if vmaf_lo < target_vmaf:
        return lo

    vmaf_hi: float | None = None  # measured at the current failing bound
    while hi - lo > 1:
        if vmaf_hi is None:
            # first round: no failing measurement yet — bisect (ab-av1's
            # first guess is the midpoint for the same reason)
            guess = (lo + hi) // 2
        else:
            # linear interpolation between the passing (vmaf_lo at lo) and
            # failing (vmaf_hi at hi) ends, in VMAF space; a flat segment
            # (equal scores) gives no direction => midpoint
            span = vmaf_lo - vmaf_hi
            if span > 0:
                frac = (vmaf_lo - target_vmaf) / span
                guess = round(lo + (hi - lo) * frac)
            else:
                guess = (lo + hi) // 2
            # safeguard: strictly inside the bracket, and the surviving
            # side after this probe must be no larger than bisection's
            # (ceil(w/2)) — on a convex or step-like curve regula falsi
            # crawls (its guesses hug the passing edge), so fall back to
            # the midpoint whenever interpolation would shrink the bracket
            # more slowly than the plain bisect would from here
            guess = max(lo + 1, min(hi - 1, guess))
            if max(guess - lo, hi - guess) > (hi - lo + 1) // 2:
                guess = (lo + hi) // 2
        value = score(guess)
        if value >= target_vmaf:
            lo, vmaf_lo = guess, value
        else:
            hi, vmaf_hi = guess, value
    return lo


def probe_crf(
    samples: list[tuple[Path, float]],
    crf: int,
    workdir: Path,
    handbrake: str,
    movie: Path,
    sample_seconds: float,
    encoder: str,
    probe_encoder_preset: str,
    crop: tuple[int, int, int, int] | None = None,
    hdr: bool = False,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    encoder_threads: int | None = None,
    use_probe_cache: bool = True,
) -> float:
    """Encodes all samples at one CRF and returns their mean VMAF score.

    The MEAN of the per-sample means governs the decision. A minimum over
    samples is monotone non-increasing in the sample count: every window
    added can only lower it or leave it unchanged, which makes it
    structurally hostile to denser sampling (#14) - the better the
    coverage, the more likely one pathological window sinks the whole
    score and the film is reported 'unreachable' when it is encodable.

    What the mean gives up: 'every probed window meets the target' is no
    longer literally true. Denser sampling reduces how likely a genuinely
    bad scene hides behind good ones, but does not eliminate it.

    Per-sample measurements are cached (probe_cache table, content-addressed
    via probe_cache_key): a sample whose identity, encode settings and
    measurement mode all match a previous run is reused instead of being
    re-encoded. A cache hit IS a fresh measurement of unchanged inputs.

    Args:
        samples (list[tuple[Path, float]]): The sample files with their
            start offsets in seconds.
        crf (int): The CRF quality value to probe.
        workdir (Path): Working directory for probe files.
        handbrake (str): Path to the HandBrakeCLI binary.
        movie (Path): The source movie (cache identity: size + mtime).
        sample_seconds (float): The sample window's length in seconds.
        encoder (str): The video encoder, e.g. 'svt_av1'.
        probe_encoder_preset (str): The PROBE's encoder speed preset (a
            measurement setting; the final encode's preset is a separate
            value, issue #22).
        crop (tuple | None): Crop bars applied to probes and measurements,
            so the decision matches the final encode.
        hdr (bool): Tone-map both sides for the VMAF measurement.
        cancel (threading.Event | None): Cooperative cancellation.
        db_file (Path | None): The database file (caching and failure
            evidence; None => uncached probing).
        encoder_threads (int | None): Thread limit for the probe encodes
            (None = no limit; must match the final encode's cap).
        use_probe_cache (bool): False bypasses the cache entirely
            (force_reprobe: the caller asked to measure everything fresh).

    Returns:
        float: The mean per-sample VMAF mean at this CRF.

    Raises:
        RuntimeError: If a sample encode or VMAF measurement failed.
        Cancelled: When the cancel event fires.
    """
    tool_versions = _tool_versions(handbrake)
    scores: list[float] = []
    for index, (sample, offset) in enumerate(samples):
        _check_cancel(cancel)
        key = probe_cache_key(
            movie,
            offset,
            sample_seconds,
            crf,
            encoder,
            probe_encoder_preset,
            crop,
            hdr,
            encoder_threads,
            tool_versions,
        )
        cached, connection = (None, None)
        if use_probe_cache:
            cached, connection = _cached_or_none(db_file, key)
        if cached is not None:
            log(f"probe cache hit (CRF {crf}, sample {index + 1}/{len(samples)})")
            scores.append(cached)
            if connection is not None:
                connection.close()
            continue
        encoded = workdir / f"probe_crf{crf}_{index}.mkv"
        command = build_encode_command(
            handbrake,
            sample,
            encoded,
            crf,
            encoder,
            probe_encoder_preset,
            crop=crop,
            encoder_threads=encoder_threads,
        )
        code, err = _run(command, cancel=cancel)
        if code == -1:
            if connection is not None:
                connection.close()
            raise Cancelled("cancelled during probe encode")
        if code != 0 or not encoded.is_file() or encoded.stat().st_size == 0:
            if connection is not None:
                connection.close()
            raise RuntimeError(f"probe encode failed (crf {crf}): {err[-400:]}")
        _check_cancel(cancel)
        score = measure_vmaf(
            encoded, sample, workdir, crop=crop, hdr=hdr, cancel=cancel, db_file=db_file
        )
        _store_probe(connection, key, score)
        encoded.unlink()
        scores.append(score)
    return sum(scores) / len(scores)


def estimate_size_factor(
    samples: list[tuple[Path, float]],
    workdir: Path,
    handbrake: str,
    crf: int,
    movie: Path,
    sample_seconds: float,
    encoder: str,
    probe_encoder_preset: str,
    crop: tuple[int, int, int, int] | None = None,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    encoder_threads: int | None = None,
    progress: ProgressCallback | None = None,
    use_probe_cache: bool = True,
) -> float:
    """Estimates the full-encode size relative to the source from one sample.

    Encodes the first sample at the given CRF and returns the size ratio
    (encoded / source). Values >= 1 mean the re-encode would not shrink the
    file => the movie is a skip candidate.

    The ratio is a byte count, so no VMAF measurement is made here: decoding
    both sides through libvmaf is the most expensive step in the probe pipeline
    and its result would be discarded. A missing or broken libvmaf must not stop
    a size-probe skip decision; movies that reach the CRF search still fail
    loudly in `probe_crf`.

    Args:
        samples (list[tuple[Path, float]]): The sample files with their
            start offsets in seconds.
        workdir (Path): Working directory for probe files.
        handbrake (str): Path to the HandBrakeCLI binary.
        crf (int): The CRF quality value to probe.
        movie (Path): The source movie (cache identity: size + mtime).
        sample_seconds (float): The sample window's length in seconds.
        encoder (str): The video encoder, e.g. 'svt_av1'.
        probe_encoder_preset (str): The PROBE's encoder speed preset (a
            measurement setting, issue #22).
        crop (tuple | None): Crop bars applied to the probe encode.
        cancel (threading.Event | None): Cooperative cancellation.
        db_file (Path | None): The database file (caching; None => uncached).
        encoder_threads (int | None): Thread limit for the probe encode.
        progress (callable | None): Callback progress(dict) for live
            step updates (flow to the UI, never persisted).
        use_probe_cache (bool): False bypasses the probe cache.

    Returns:
        float: The size ratio of the probe encode (encoded / sample source).

    Raises:
        RuntimeError: If the probe encode failed.
        Cancelled: When the cancel event fires.
    """
    _check_cancel(cancel)
    sample, offset = samples[0]
    encoded = workdir / f"size_probe_crf{crf}.mkv"
    if progress:
        progress({"stage": "probe", "detail": f"size probe (CRF {crf})"})
    # the size ratio is measured without VMAF: the measurement inputs (hdr,
    # libvmaf version) cannot change it and stay out of the key
    key = probe_cache_key(
        movie,
        offset,
        sample_seconds,
        crf,
        encoder,
        probe_encoder_preset,
        crop,
        hdr=False,
        encoder_threads=encoder_threads,
        tool_versions=_tool_versions(handbrake),
        kind="size",
    )
    cached, connection = (None, None)
    if use_probe_cache:
        cached, connection = _cached_or_none(db_file, key)
    if cached is not None:
        log(f"probe cache hit (size probe, CRF {crf})")
        if connection is not None:
            connection.close()
        return cached
    command = build_encode_command(
        handbrake,
        sample,
        encoded,
        crf,
        encoder,
        probe_encoder_preset,
        crop=crop,
        encoder_threads=encoder_threads,
    )
    code, err = _run(command, cancel=cancel)
    if code == -1:
        if connection is not None:
            connection.close()
        raise Cancelled("cancelled during size probe")
    if code != 0 or not encoded.is_file() or encoded.stat().st_size == 0:
        if connection is not None:
            connection.close()
        raise RuntimeError(f"size probe encode failed (crf {crf}): {err[-400:]}")
    try:
        factor = encoded.stat().st_size / sample.stat().st_size
    finally:
        encoded.unlink()
    _store_probe(connection, key, factor)
    return factor


def find_optimal_crf(
    movie: Path,
    workdir: Path,
    handbrake: str,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int | None = None,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    sample_every: float | None = DEFAULT_SAMPLE_EVERY,
    encoder: str = "svt_av1",
    probe_encoder_preset: str = "5",
    crop: tuple[int, int, int, int] | None = None,
    hdr: bool = False,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    encoder_threads: int | None = None,
    progress: ProgressCallback | None = None,
    use_probe_cache: bool = True,
    samples: list[tuple[Path, float]] | None = None,
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
        encoder (str): The video encoder, e.g. 'svt_av1'.
        probe_encoder_preset (str): The PROBE's encoder speed preset (a
            measurement setting, issue #22).
        crop (tuple | None): Crop bars (top, bottom, left, right) applied to
            probes and the decision, matching the final encode.
        hdr (bool): Tone-map both sides for the VMAF measurement.
        cancel (threading.Event | None): Cooperative cancellation.
        db_file (Path | None): The database file for failure-evidence logs.
        encoder_threads (int | None): Thread limit for the probe encodes
            (None = no limit; must match the final encode's cap).
        progress (callable | None): Callback progress(dict) for live
            step updates (flow to the UI, never persisted).

    Returns:
        tuple: The chosen CRF, the VMAF measured at that CRF and all probes.

    Raises:
        RuntimeError: If sampling or probing failed.
        Cancelled: When the cancel event fires.
    """
    if samples is None:
        samples = [
            (s.resolve(), offset)
            for s, offset in extract_samples(
                movie,
                workdir,
                sample_count_for(probe_duration_seconds(movie), sample_every, sample_count),
                sample_seconds,
                cancel=cancel,
                progress=progress,
            )
        ]

    probes: list[SampleProbe] = []

    def check(crf: int) -> float:
        _check_cancel(cancel)
        if progress:
            progress({"stage": "probe", "detail": f"probe encode (CRF {crf})"})
        vmaf = probe_crf(
            samples,
            crf,
            workdir,
            handbrake,
            movie,
            sample_seconds,
            encoder,
            probe_encoder_preset,
            crop=crop,
            hdr=hdr,
            cancel=cancel,
            db_file=db_file,
            encoder_threads=encoder_threads,
            use_probe_cache=use_probe_cache,
        )
        probes.append(SampleProbe(crf=crf, vmaf=round(vmaf, 2)))
        if progress:
            progress(
                {
                    "stage": "probe",
                    "detail": f"CRF {crf}: VMAF {round(vmaf, 2)}",
                    "probes_done": len(probes),
                }
            )
        return vmaf

    lo, hi = crf_range
    chosen = bisect_crf(check, lo, hi, target_vmaf)
    achieved = next(p.vmaf for p in reversed(probes) if p.crf == chosen)
    return chosen, achieved, probes


def find_optimal_crf_and_skip(
    movie: Path,
    workdir: Path,
    handbrake: str,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int | None = None,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    sample_every: float | None = DEFAULT_SAMPLE_EVERY,
    encoder: str = "svt_av1",
    probe_encoder_preset: str = "5",
    crop: tuple[int, int, int, int] | None = None,
    hdr: bool = False,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    encoder_threads: int | None = None,
    progress: ProgressCallback | None = None,
    use_probe_cache: bool = True,
    samples: list[tuple[Path, float]] | None = None,
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
        encoder (str): The video encoder, e.g. 'svt_av1'.
        probe_encoder_preset (str): The PROBE's encoder speed preset (a
            measurement setting, issue #22).
        crop (tuple | None): Crop bars (top, bottom, left, right) applied to
            probes and the decision, matching the final encode.
        hdr (bool): Tone-map both sides for the VMAF measurement.
        cancel (threading.Event | None): Cooperative cancellation.
        db_file (Path | None): The database file for failure-evidence logs.
        encoder_threads (int | None): Thread limit for the probe encodes
            (None = no limit; must match the final encode's cap).
        use_probe_cache (bool): False bypasses the probe-encode cache
            (force_reprobe: the caller asked to measure everything fresh).

    Returns:
        tuple: The chosen CRF, the VMAF at that CRF, all probes and a flag
            that is True when the movie should NOT be re-encoded.
    """
    if samples is None:
        samples = [
            (s.resolve(), offset)
            for s, offset in extract_samples(
                movie,
                workdir,
                sample_count_for(probe_duration_seconds(movie), sample_every, sample_count),
                sample_seconds,
                cancel=cancel,
                progress=progress,
            )
        ]

    # highest CRF (smallest size) of the search range as the pessimistic
    # bound: if even the most aggressive setting cannot shrink the file,
    # no CRF in the range can
    factor = estimate_size_factor(
        samples,
        workdir,
        handbrake,
        crf_range[1],
        movie,
        sample_seconds,
        encoder,
        probe_encoder_preset,
        crop=crop,
        cancel=cancel,
        db_file=db_file,
        encoder_threads=encoder_threads,
        progress=progress,
        use_probe_cache=use_probe_cache,
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
        probe_encoder_preset=probe_encoder_preset,
        samples=samples,
        crop=crop,
        hdr=hdr,
        cancel=cancel,
        db_file=db_file,
        encoder_threads=encoder_threads,
        progress=progress,
        use_probe_cache=use_probe_cache,
    )
    return chosen, achieved, probes, False


def plan_quality(
    input_file: Path,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int | None = None,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    sample_every: float | None = DEFAULT_SAMPLE_EVERY,
    encoder: str = "svt_av1",
    probe_encoder_preset: str = "5",
    encoder_preset: str = "3",
    remove_bars: bool = False,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    encoder_threads: int | None = None,
    progress: ProgressCallback | None = None,
    use_probe_cache: bool = True,
    samples: list[tuple[Path, float]] | None = None,
    min_source_height: int = 0,
    min_source_size_gb: float = 0.0,
    candidate_codecs: tuple[str, ...] = CANDIDATE_CODECS,
    crop_sample_count: int = DEFAULT_CROP_SAMPLE_COUNT,
) -> QualityResult:
    """Probes a movie and predicts the outcome without encoding it.

    Runs the exact same probing pipeline as encode_with_quality (samples,
    size estimate, CRF bisect) but stops before the full encode. The result
    carries the predicted CRF, the measured VMAF and a size estimate based
    on the sample probes.

    Args:
        input_file (Path): The file to analyze.
        target_vmaf (float): The VMAF quality floor to meet.
        crf_range (tuple[int, int]): The (lowest, highest) CRF to search.
        sample_count (int): The number of samples to probe.
        sample_seconds (float): The length of each sample in seconds.
        encoder (str): The video encoder, e.g. 'svt_av1'. for HDR the engine
            switches to the 10-bit variant and the result carries it.
        probe_encoder_preset (str): The PROBE's encoder speed preset (a
            measurement setting, issue #22); the plan's fingerprint keys
            on this value.
        encoder_preset (str): The FINAL encode's speed preset (an output
            setting, issue #22); plan never encodes, so this is recorded
            on the result for provenance only.
        remove_bars (bool): Detect and crop stable black bars (opt-in).
        cancel (threading.Event | None): Cooperative cancellation.
        db_file (Path | None): The database file, used to locate the logs
            directory for failure evidence (None => cwd metadata.db).
        encoder_threads (int | None): Thread limit for the probe encodes
            (None = no limit; must match a later encode's cap).
        progress (callable | None): Callback progress(dict) for live step
            updates of the probing pipeline (flow to the UI, never
            persisted — the same events the console prints as log lines).
        min_source_height (int): Skip sources below this height (0 = gate
            off). A lower-resolution source has too few pixels to save
            meaningfully, so the probe cost buys nothing.
        min_source_size_gb (float): Skip sources below this size (0 = gate
            off). Probe cost is roughly constant per file while the payoff
            scales with size, so the smallest files have the worst
            cost/benefit ratio.
        candidate_codecs (tuple[str, ...]): Source codecs that are candidates
            on size alone. A modern codec (HEVC/AV1) is already efficient,
            so it is a candidate only when it has bars to remove; anything
            older is never re-encoded.
        crop_sample_count (int): cropdetect windows per movie. More
            samples can only shrink the crop once the bars are the ones
            common to every sample, the safe direction for an
            irreversible crop.

    Returns:
        QualityResult: The prediction (success=False with a skipped_reason
            for skip decisions; input/output sizes carry the size estimate
            as output_size = input_size * estimated factor).

    Raises:
        FileNotFoundError: If the input file does not exist.
        RuntimeError: If HandBrakeCLI is not installed or probing failed.
        Cancelled: When the cancel event fires.
    """
    if not input_file.is_file():
        raise FileNotFoundError(f"Input file '{input_file}' does not exist!")

    hdr = is_hdr_file(input_file)
    # the effective decision inputs: for HDR the engine overrides the
    # target/range and switches to the 10-bit encoder — every downstream
    # call and the result's fingerprint fields carry these OVERRIDDEN values
    effective_encoder = encoder_for(hdr)
    if hdr:
        # the tone-mapped measurement scale sits slightly lower; use the
        # calibrated target and the deeper CRF range for 4K headroom
        target_vmaf = DEFAULT_HDR_TARGET_VMAF
        crf_range = DEFAULT_HDR_CRF_RANGE

    duration = probe_duration_seconds(input_file)
    sample_count = sample_count_for(duration, sample_every, sample_count)
    if duration is not None and duration < MIN_ENCODE_SECONDS:
        return QualityResult(
            input=input_file,
            output=input_file,
            success=False,
            target_vmaf=target_vmaf,
            error=f"duration {duration:.0f}s is below the {MIN_ENCODE_SECONDS:.0f}s minimum",
            skipped_reason="too short",
            remove_bars=remove_bars,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            hdr=hdr,
            encoder=effective_encoder,
        )

    height = source_height(input_file)
    if height is not None and height < min_source_height:
        return QualityResult(
            input=input_file,
            output=input_file,
            success=False,
            target_vmaf=target_vmaf,
            error=(
                f"height {height}p is below the {min_source_height}p minimum "
                "(a lower-resolution source saves too little to be worth the probe)"
            ),
            skipped_reason="resolution too low",
            remove_bars=remove_bars,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            hdr=hdr,
            encoder=effective_encoder,
        )

    # the size gate is the cheapest of the remaining checks: it reads one
    # stat, and probe cost is roughly constant while the payoff scales with
    # the file's size
    size_gb = input_file.stat().st_size / (1024**3)
    if min_source_size_gb > 0 and size_gb < min_source_size_gb:
        return QualityResult(
            input=input_file,
            output=input_file,
            success=False,
            target_vmaf=target_vmaf,
            error=(
                f"size {size_gb:.2f} GB is below the {min_source_size_gb:.2f} GB "
                "minimum (too little to save for the probe cost)"
            ),
            skipped_reason="below min size",
            remove_bars=remove_bars,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            hdr=hdr,
            encoder=effective_encoder,
        )

    # the codec gate is the product rule: an H.264 source is a candidate on
    # size alone, while a modern codec is only worth re-encoding when there
    # are bars to remove (PRODUCT.md). An unknown codec is probed, never
    # excluded - an unreadable value must not silently drop a file.
    # a modern codec is already efficient, so size alone never triggers an
    # encode: it is a candidate only when bars are to be removed, and the
    # crop that justifies the encode is then actually applied
    codec = source_codec(input_file)
    modern_bars: tuple[int, int, int, int] | None = None
    if codec in MODERN_CODECS:
        if not remove_bars:
            return QualityResult(
                input=input_file,
                output=input_file,
                success=False,
                target_vmaf=target_vmaf,
                error=(
                    f"source codec '{codec}' is already efficient; it is re-encoded "
                    "only to remove black bars (use --remove-bars)"
                ),
                skipped_reason="already efficient",
                remove_bars=remove_bars,
                crf_range=crf_range,
                sample_count=sample_count,
                sample_seconds=sample_seconds,
                hdr=hdr,
                encoder=effective_encoder,
            )
        probe_dir = Path(tempfile.mkdtemp(prefix="metadata_bars_"))
        try:
            modern_bars = detect_crop_rect(input_file, probe_dir, count=crop_sample_count)
        finally:
            shutil.rmtree(probe_dir, ignore_errors=True)
        if modern_bars is None:
            return QualityResult(
                input=input_file,
                output=input_file,
                success=False,
                target_vmaf=target_vmaf,
                error=(
                    f"source codec '{codec}' is already efficient and no black bars "
                    "were detected, so there is nothing to re-encode"
                ),
                skipped_reason="already efficient",
                remove_bars=remove_bars,
                crf_range=crf_range,
                sample_count=sample_count,
                sample_seconds=sample_seconds,
                hdr=hdr,
                encoder=effective_encoder,
            )

    if codec is not None and codec not in candidate_codecs and codec not in MODERN_CODECS:
        return QualityResult(
            input=input_file,
            output=input_file,
            success=False,
            target_vmaf=target_vmaf,
            error=(
                f"source codec '{codec}' is below the {MIN_SOURCE_CODEC} minimum "
                "(an older codec is not re-encoded)"
            ),
            skipped_reason="source codec not a candidate",
            remove_bars=remove_bars,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            hdr=hdr,
            encoder=effective_encoder,
        )

    handbrake = find_handbrake()
    if handbrake is None:
        raise RuntimeError(
            f"'{HANDBRAKE_BINARY}' not found! Install HandBrake CLI "
            "(https://handbrake.fr/docs/en/latest/cli/cli-options.html)"
        )

    workdir = Path(tempfile.mkdtemp(prefix="metadata_quality_"))
    try:
        # the codec gate already detected the bars for a modern-codec source;
        # reuse that crop so the pipeline does not sample them a second time
        crop = (
            modern_bars
            if modern_bars is not None
            else (
                detect_crop_rect(input_file, workdir, count=crop_sample_count)
                if remove_bars
                else None
            )
        )
        log(f"Probing '{input_file.name}' (target VMAF {target_vmaf})...")
        # extract the windows once and hand the same list to the search and
        # the reported size factor: each extraction decodes and writes every
        # sample, so doing it per phase encoded each window three times
        samples = [
            (s.resolve(), offset)
            for s, offset in extract_samples(
                input_file, workdir, sample_count, sample_seconds, cancel=cancel, progress=progress
            )
        ]
        crf, achieved, probes, skip = find_optimal_crf_and_skip(
            input_file,
            workdir,
            handbrake,
            target_vmaf=target_vmaf,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            encoder=effective_encoder,
            probe_encoder_preset=probe_encoder_preset,
            crop=crop,
            hdr=hdr,
            cancel=cancel,
            db_file=db_file,
            encoder_threads=encoder_threads,
            progress=progress,
            use_probe_cache=use_probe_cache,
            samples=samples,
        )

        input_size = input_file.stat().st_size
        factor = estimate_size_factor(
            samples,
            workdir,
            handbrake,
            crf,
            input_file,
            sample_seconds,
            effective_encoder,
            probe_encoder_preset,
            crop=crop,
            cancel=cancel,
            db_file=db_file,
            encoder_threads=encoder_threads,
            progress=progress,
            use_probe_cache=use_probe_cache,
        )

        if skip:
            return QualityResult(
                input=input_file,
                output=input_file,
                success=False,
                target_vmaf=target_vmaf,
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probes=probes,
                input_size=input_size,
                output_size=input_size,
                error="skipped: re-encode would not shrink the file",
                skipped_reason="no size gain",
                encoder_preset=encoder_preset,
                probe_encoder_preset=probe_encoder_preset,
                remove_bars=remove_bars,
                crop=crop,
                crf_range=crf_range,
                sample_count=sample_count,
                sample_seconds=sample_seconds,
                hdr=hdr,
                encoder=effective_encoder,
            )
        if achieved < target_vmaf and crf == crf_range[0]:
            return QualityResult(
                input=input_file,
                output=input_file,
                success=False,
                target_vmaf=target_vmaf,
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probes=probes,
                input_size=input_size,
                output_size=input_size,
                error=(
                    f"skipped: target VMAF {target_vmaf} unreachable "
                    f"(best {achieved} at CRF {crf_range[0]})"
                ),
                skipped_reason="target unreachable",
                encoder_preset=encoder_preset,
                probe_encoder_preset=probe_encoder_preset,
                remove_bars=remove_bars,
                crop=crop,
                crf_range=crf_range,
                sample_count=sample_count,
                sample_seconds=sample_seconds,
                hdr=hdr,
                encoder=effective_encoder,
            )

        return QualityResult(
            input=input_file,
            output=input_file,
            success=True,
            target_vmaf=target_vmaf,
            chosen_crf=crf,
            achieved_vmaf=achieved,
            probes=probes,
            input_size=input_size,
            output_size=int(input_size * factor),
            error=None,
            encoder_preset=encoder_preset,
            probe_encoder_preset=probe_encoder_preset,
            remove_bars=remove_bars,
            crop=crop,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            hdr=hdr,
            encoder=effective_encoder,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _plan_crop(plan: sqlite3.Row) -> tuple[int, int, int, int] | None:
    """Parses a stored plan's crop rect ('top,bottom,left,right').

    Args:
        plan (sqlite3.Row): The plan row.

    Returns:
        tuple[int, int, int, int] | None: The crop rect, or None.
    """
    raw = plan["crop"]
    if not raw:
        return None
    parts = [int(v) for v in str(raw).split(",")]
    if len(parts) != 4:
        return None
    return (parts[0], parts[1], parts[2], parts[3])


def _plan_probes(plan: sqlite3.Row) -> list[SampleProbe]:
    """Restores a stored plan's probe list from its JSON column.

    Args:
        plan (sqlite3.Row): The plan row.

    Returns:
        list[SampleProbe]: The probes that backed the plan's decision.
    """
    try:
        pairs = json.loads(plan["probes"] or "[]")
    except (TypeError, ValueError):
        return []
    return [SampleProbe(crf=int(c), vmaf=float(v)) for c, v in pairs]


def _lookup_plan(
    db_file: Path,
    input_file: Path,
    *,
    encoder: str,
    encoder_preset: str,
    target_vmaf: float,
    crf_range: tuple[int, int],
    sample_count: int,
    sample_seconds: float,
    remove_bars: bool,
    crop_sample_count: int = DEFAULT_CROP_SAMPLE_COUNT,
) -> sqlite3.Row | None:
    """Consults the plan cache for a valid plan of this movie+settings.

    Opens a short-lived read connection (the engine stays connection-free;
    db_file already locates the logs dir, so it already knows the db).
    Any mismatch, missing file row or vanished movie returns None — the
    caller re-probes, never guesses.

    Args:
        db_file (Path): The database file.
        input_file (Path): The movie to encode.
        encoder (str): The pending encode's effective encoder.
        encoder_preset (str): The pending encode's preset.
        target_vmaf (float): The pending encode's (HDR-overridden) target.
        crf_range (tuple[int, int]): The pending encode's (HDR-overridden) range.
        sample_count (int): The pending encode's sample count.
        sample_seconds (float): The pending encode's sample length.
        remove_bars (bool): The pending encode's remove_bars setting.
        crop_sample_count (int): The pending encode's crop sample count.

    Returns:
        sqlite3.Row | None: The valid plan row, or None.
    """
    from shrinkray import db as _db

    if not input_file.is_file():
        return None
    connection = _db.connect(db_file)
    try:
        return _db.get_valid_plan(
            connection,
            input_file,
            encoder=encoder,
            encoder_preset=encoder_preset,
            target_vmaf=target_vmaf,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            remove_bars=remove_bars,
            crop_sample_count=crop_sample_count,
            vmaf_statistic=VMAF_STATISTIC,
        )
    finally:
        connection.close()


def _probe_movie(
    input_file: Path,
    target_vmaf: float,
    crf_range: tuple[int, int],
    sample_count: int,
    sample_seconds: float,
    encoder: str,
    probe_encoder_preset: str,
    remove_bars: bool,
    hdr: bool = False,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    encoder_threads: int | None = None,
    progress: ProgressCallback | None = None,
    use_probe_cache: bool = True,
    crop: tuple[int, int, int, int] | None = None,
    crop_detected: bool = False,
    crop_sample_count: int = DEFAULT_CROP_SAMPLE_COUNT,
) -> tuple[int, float, list[SampleProbe], tuple[int, int, int, int] | None]:
    """Shared probing pipeline of plan_quality and encode_with_quality.

    Args:
        input_file (Path): The file to probe.
        target_vmaf (float): The VMAF quality floor to meet.
        crf_range (tuple[int, int]): The (lowest, highest) CRF to search.
        sample_count (int): The number of samples to probe.
        sample_seconds (float): The length of each sample in seconds.
        encoder (str): The video encoder, e.g. 'svt_av1'.
        probe_encoder_preset (str): The PROBE's encoder speed preset (a
            measurement setting, issue #22).
        remove_bars (bool): Detect and crop stable black bars.
        crop (tuple[int, int, int, int] | None): A crop the caller already
            detected (the codec gate's), applied instead of sampling again.
        crop_detected (bool): True when crop was supplied by the caller,
            so the pipeline must not detect it a second time.
        crop_sample_count (int): cropdetect windows per movie, used only
            when the pipeline detects the bars itself.
        hdr (bool): Tone-map both sides for the VMAF measurement.
        cancel (threading.Event | None): Cooperative cancellation.
        db_file (Path | None): The database file for failure-evidence logs.
        encoder_threads (int | None): Thread limit for the probe encodes
            (None = no limit; must match the final encode's cap).
        progress (callable | None): Callback progress(dict) for live step
            updates of the probing pipeline (flow to the UI, never
            persisted).

    Returns:
        tuple: The chosen CRF, its VMAF, all probes and the crop bars
            (None when no crop is applied).

    Raises:
        RuntimeError: If HandBrakeCLI is not installed or probing failed.
        Cancelled: When the cancel event fires.
    """
    _check_cancel(cancel)
    handbrake = find_handbrake()
    if handbrake is None:
        raise RuntimeError(
            f"'{HANDBRAKE_BINARY}' not found! Install HandBrake CLI "
            "(https://handbrake.fr/docs/en/latest/cli/cli-options.html)"
        )

    workdir = Path(tempfile.mkdtemp(prefix="metadata_quality_"))
    try:
        # a crop the codec gate already detected is reused rather than
        # sampled again: the gate had to detect it to decide, and the same
        # bars must be applied to the encode that justifies it
        if not crop_detected:
            crop = (
                detect_crop_rect(input_file, workdir, count=crop_sample_count)
                if remove_bars
                else None
            )
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
            probe_encoder_preset=probe_encoder_preset,
            crop=crop,
            hdr=hdr,
            cancel=cancel,
            db_file=db_file,
            encoder_threads=encoder_threads,
            progress=progress,
            use_probe_cache=use_probe_cache,
        )
        if skip:
            raise SkipEncode(
                "re-encode would not shrink the file",
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probe_count=len(probes),
            )
        if achieved < target_vmaf and crf == crf_range[0]:
            raise SkipEncode(
                f"target VMAF {target_vmaf} unreachable (best {achieved} at CRF {crf_range[0]})",
                chosen_crf=crf,
                achieved_vmaf=achieved,
                probe_count=len(probes),
            )
        return crf, achieved, probes, crop
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class SkipEncode(Exception):
    """Raised when the probing decides a movie should not be encoded.

    The skip carries its evidence - the best CRF, the VMAF measured
    there and how many probes were spent - so a later run can tell
    'already probed at the floor and failed' from 'never probed'
    instead of re-deriving the same verdict every run.

    Attributes:
        chosen_crf (int | None): The best CRF the search reached.
        achieved_vmaf (float | None): The VMAF measured there.
        probe_count (int): How many CRF probes were spent.
    """

    def __init__(
        self,
        message: str,
        chosen_crf: int | None = None,
        achieved_vmaf: float | None = None,
        probe_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.chosen_crf = chosen_crf
        self.achieved_vmaf = achieved_vmaf
        self.probe_count = probe_count


class Cancelled(Exception):
    """Raised inside the engine when the job's cancel event fires.

    The worker turns this into a proper CANCELLED job state; partial
    artifacts (probe files, half-written output) are the caller's to
    clean up (the sample workdir is removed by its own finally).
    """


def _check_cancel(cancel: threading.Event | None) -> None:
    """Raises Cancelled when the cancel event is set.

    Args:
        cancel (threading.Event | None): The job's cancel event (None
            disables the check, e.g. for CLI one-shots).

    Raises:
        Cancelled: When the event is set.
    """
    if cancel is not None and cancel.is_set():
        raise Cancelled("cancelled by user")


def encode_with_quality(
    input_file: Path,
    output_file: Path,
    target_vmaf: float = DEFAULT_TARGET_VMAF,
    crf_range: tuple[int, int] = DEFAULT_CRF_RANGE,
    sample_count: int | None = None,
    sample_seconds: float = DEFAULT_SAMPLE_SECONDS,
    sample_every: float | None = DEFAULT_SAMPLE_EVERY,
    encoder: str = "svt_av1",
    probe_encoder_preset: str = "5",
    encoder_preset: str = "3",
    remove_bars: bool = False,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    db_file: Path | None = None,
    encoder_threads: int | None = None,
    verify: bool = False,
    force_reprobe: bool = False,
    use_probe_cache: bool = True,
    min_source_height: int = 0,
    min_source_size_gb: float = 0.0,
    candidate_codecs: tuple[str, ...] = CANDIDATE_CODECS,
    crop_sample_count: int = DEFAULT_CROP_SAMPLE_COUNT,
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
        encoder (str): The video encoder, e.g. 'svt_av1'.
        probe_encoder_preset (str): The PROBE's speed preset (a measurement
            setting, issue #22); the probes and the plan fingerprint use it.
        encoder_preset (str): The FINAL encode's speed preset (an output
            setting, issue #22).
        remove_bars (bool): Detect and crop stable black bars (opt-in, the
            crop is irreversible).
        progress (callable | None): Callback progress(dict) for live updates
            of the running encode's percentage.
        cancel (threading.Event | None): Cooperative cancellation (checked
            between probe steps; the final encode aborts by termination).
        db_file (Path | None): The database file, used to locate the logs
            directory for failure evidence (None => cwd metadata.db).
        encoder_threads (int | None): Thread limit for the probe encodes
            AND the final encode (None = no limit).
        min_source_height (int): Skip sources below this height (0 = gate
            off).
        min_source_size_gb (float): Skip sources below this size (0 = gate
            off).
        candidate_codecs (tuple[str, ...]): Source codecs that are
            candidates on size alone.
        crop_sample_count (int): cropdetect windows per movie.
        verify (bool): Run a full-file VMAF verification after a successful
            encode (opt-in: hours of decode for a large movie; sub-sampled
            1/10 above VERIFY_SUBSAMPLE_THRESHOLD_BYTES).
        force_reprobe (bool): Bypass the plan lookup entirely and run the
            normal probing pipeline even when a valid plan exists (CLI:
            --replan). It also bypasses the probe-encode cache: everything
            is measured fresh, nothing is reused. Plans are only consulted
            for explicit encode runs; watch mode passes True to stay
            independent of plan staleness (and therefore also re-probes
            fresh).

    Returns:
        QualityResult: The result of the encode.

    Raises:
        FileNotFoundError: If the input file does not exist.
        RuntimeError: If HandBrakeCLI is not installed.
        Cancelled: When the cancel event fires.
    """
    if not input_file.is_file():
        raise FileNotFoundError(f"Input file '{input_file}' does not exist!")

    import time as _time

    run_started = _time.perf_counter()
    snapshot = _source_snapshot(input_file)
    hdr = is_hdr_file(input_file)
    # the effective decision inputs: for HDR the engine overrides the
    # target/range and switches to the 10-bit encoder (same rule as
    # plan_quality, so a stored HDR plan is found under its real encoder)
    effective_encoder = encoder_for(hdr)
    if hdr:
        target_vmaf = DEFAULT_HDR_TARGET_VMAF
        crf_range = DEFAULT_HDR_CRF_RANGE

    duration = probe_duration_seconds(input_file)
    sample_count = sample_count_for(duration, sample_every, sample_count)
    if duration is not None and duration < MIN_ENCODE_SECONDS:
        return QualityResult(
            input=input_file,
            output=output_file,
            success=False,
            target_vmaf=target_vmaf,
            error=f"duration {duration:.0f}s is below the {MIN_ENCODE_SECONDS:.0f}s minimum",
            skipped_reason="too short",
            hdr=hdr,
            encoder=effective_encoder,
        )

    # the resolution gate decides from cached metadata, before the size probe
    # and the CRF search spend minutes on a source whose pixels bound the saving
    height = (snapshot or {}).get("height")
    if height is not None and height < min_source_height:
        return QualityResult(
            input=input_file,
            output=output_file,
            success=False,
            target_vmaf=target_vmaf,
            error=(
                f"height {height}p is below the {min_source_height}p minimum "
                "(a lower-resolution source saves too little to be worth the probe)"
            ),
            skipped_reason="resolution too low",
            hdr=hdr,
            encoder=effective_encoder,
        )

    size_gb = input_file.stat().st_size / (1024**3)
    if min_source_size_gb > 0 and size_gb < min_source_size_gb:
        return QualityResult(
            input=input_file,
            output=output_file,
            success=False,
            target_vmaf=target_vmaf,
            error=(
                f"size {size_gb:.2f} GB is below the {min_source_size_gb:.2f} GB "
                "minimum (too little to save for the probe cost)"
            ),
            skipped_reason="below min size",
            hdr=hdr,
            encoder=effective_encoder,
        )

    # an H.264 source is a candidate on size alone; a modern codec only
    # when bars are to be removed, and then the crop is applied
    codec = source_codec(input_file)
    modern_bars: tuple[int, int, int, int] | None = None
    if codec in MODERN_CODECS:
        if not remove_bars:
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                error=(
                    f"source codec '{codec}' is already efficient; it is re-encoded "
                    "only to remove black bars (use --remove-bars)"
                ),
                skipped_reason="already efficient",
                hdr=hdr,
                encoder=effective_encoder,
            )
        bars_dir = Path(tempfile.mkdtemp(prefix="metadata_bars_"))
        try:
            modern_bars = detect_crop_rect(input_file, bars_dir, count=crop_sample_count)
        finally:
            shutil.rmtree(bars_dir, ignore_errors=True)
        if modern_bars is None:
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                error=(
                    f"source codec '{codec}' is already efficient and no black bars "
                    "were detected, so there is nothing to re-encode"
                ),
                skipped_reason="already efficient",
                hdr=hdr,
                encoder=effective_encoder,
            )

    if codec is not None and codec not in candidate_codecs and codec not in MODERN_CODECS:
        return QualityResult(
            input=input_file,
            output=output_file,
            success=False,
            target_vmaf=target_vmaf,
            error=(
                f"source codec '{codec}' is below the {MIN_SOURCE_CODEC} minimum "
                "(an older codec is not re-encoded)"
            ),
            skipped_reason="source codec not a candidate",
            hdr=hdr,
            encoder=effective_encoder,
        )

    # disk-space preflight: the honest worst case for one encode is its
    # input (CRF 0 can grow slightly above it); failing 90 minutes into a
    # 4-hour encode on a full drive is the failure mode this prevents
    from shrinkray.encode import check_disk_space

    ok, space_message = check_disk_space(output_file.parent, input_file.stat().st_size)
    if not ok:
        return QualityResult(
            input=input_file,
            output=output_file,
            success=False,
            target_vmaf=target_vmaf,
            error=space_message,
            hdr=hdr,
            encoder=effective_encoder,
        )
    if space_message:
        log(f"Warning: {space_message}")

    # plan-result reuse ("quicksave"): plan_quality and the probe pipeline
    # are the same deterministic computation - a valid stored plan replaces
    # probing entirely. ANY fingerprint mismatch (file changed, different
    # settings) fails safe to the normal probing path. The lookup uses the
    # OVERRIDDEN target/range so HDR consistency falls out of the same rule.
    plan = None
    if db_file is not None and not force_reprobe:
        # the plan fingerprint keys on the PROBE preset (issue #22 decision):
        # a stored plan is a probe result, its CRF depends on the probe
        # preset only - changing the final preset reuses the plan
        plan = _lookup_plan(
            db_file,
            input_file,
            encoder=effective_encoder,
            encoder_preset=probe_encoder_preset,
            target_vmaf=target_vmaf,
            crf_range=crf_range,
            sample_count=sample_count,
            sample_seconds=sample_seconds,
            remove_bars=remove_bars,
            crop_sample_count=crop_sample_count,
        )
    crf_source: str | None = None
    plan_created_at: str | None = None
    if plan is not None:
        if plan["skipped_reason"]:
            # the plan already rejected this movie - trust the verdict,
            # no probing, no encode. The skip decision is as valuable
            # as the CRF.
            reason = str(plan["skipped_reason"])
            if progress is not None:
                progress(
                    {
                        "stage": "probe",
                        "detail": f"using plan (probed {plan['created_at']}) · {reason}",
                    }
                )
            log(f"Using plan for '{input_file.name}' (probed {plan['created_at']}): {reason}")
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                chosen_crf=plan["chosen_crf"],
                achieved_vmaf=plan["achieved_vmaf"],
                probes=_plan_probes(plan),
                input_size=input_file.stat().st_size,
                output_size=input_file.stat().st_size,
                error=f"skipped: {reason}",
                skipped_reason=reason,
                encoder_preset=encoder_preset,
                probe_encoder_preset=probe_encoder_preset,
                remove_bars=remove_bars,
                crop=None,
                crf_range=crf_range,
                sample_count=sample_count,
                sample_seconds=sample_seconds,
                crf_source="plan",
                plan_created_at=plan["created_at"],
                hdr=hdr,
                encoder=effective_encoder,
            )
        crf = int(plan["chosen_crf"])
        achieved = float(plan["achieved_vmaf"] or 0.0)
        probes = _plan_probes(plan)
        crop = _plan_crop(plan) if remove_bars else None
        probe_seconds = 0.0
        crf_source = "plan"
        plan_created_at = plan["created_at"]
        if progress is not None:
            progress(
                {
                    "stage": "probe",
                    "detail": f"using plan (probed {plan['created_at']}) · CRF {crf}",
                }
            )
        log(f"Using plan for '{input_file.name}' (probed {plan['created_at']}): CRF {crf}")
    else:
        try:
            crf, achieved, probes, crop = _probe_movie(
                input_file,
                target_vmaf,
                crf_range,
                sample_count,
                sample_seconds,
                effective_encoder,
                probe_encoder_preset,
                remove_bars,
                hdr=hdr,
                cancel=cancel,
                db_file=db_file,
                encoder_threads=encoder_threads,
                progress=progress,
                # force_reprobe means EVERYTHING is measured fresh: the plan
                # cache above is bypassed, and so is the probe-encode cache
                use_probe_cache=not force_reprobe,
                crop=modern_bars,
                crop_detected=modern_bars is not None,
            )
        except SkipEncode as e:
            return QualityResult(
                input=input_file,
                output=output_file,
                success=False,
                target_vmaf=target_vmaf,
                error=f"skipped: {e}",
                chosen_crf=e.chosen_crf,
                achieved_vmaf=e.achieved_vmaf,
                probes=[],
                input_size=input_file.stat().st_size,
                output_size=input_file.stat().st_size,
                skipped_reason=(
                    "target unreachable" if "unreachable" in str(e) else "no size gain"
                ),
                encoder_preset=encoder_preset,
                probe_encoder_preset=probe_encoder_preset,
                remove_bars=remove_bars,
                crf_range=crf_range,
                sample_count=sample_count,
                sample_seconds=sample_seconds,
                crf_source="probe",
                hdr=hdr,
                encoder=effective_encoder,
            )
        crf_source = "probe"
        probe_seconds = _time.perf_counter() - run_started

    log(f"Chosen CRF {crf} (VMAF {achieved})")
    handbrake = find_handbrake()
    if handbrake is None:  # already checked in _probe_movie, for mypy
        raise RuntimeError(f"'{HANDBRAKE_BINARY}' not found!")
    # the FINAL encode runs the final preset (an output setting, issue #22):
    # the artifact is what gets kept, so it spends the time; the probes ran
    # at the probe preset (a measurement setting)
    command = build_encode_command(
        handbrake,
        input_file,
        output_file,
        crf,
        effective_encoder,
        encoder_preset,
        crop=crop,
        encoder_threads=encoder_threads,
    )
    # always stream the final encode: the streamed loop is the disk watchdog
    # (free-space floor => cancel instead of filling the drive). Callers
    # without a progress interest get a no-op callback; the events are
    # identical either way.
    if progress is None:
        progress = lambda data: None  # noqa: E731
    encode_result = _encode_with_command(
        input_file, output_file, command, progress=progress, cancel=cancel, db_file=db_file
    )
    if encode_result.cancelled:
        # _encode_with_command already removed the partial output; only the
        # engine-level skip-vs-fail classification remains
        raise Cancelled(encode_result.error or "cancelled during final encode")
    if not encode_result.success:
        return QualityResult(
            input=input_file,
            output=output_file,
            success=False,
            target_vmaf=target_vmaf,
            chosen_crf=crf,
            achieved_vmaf=achieved,
            probes=probes,
            error=encode_result.error,
        )
    # success path: skip the verification below (already done)
    verified_vmaf: float | None = None
    verified_subsample: int | None = None
    if verify:
        # full-file verification: the ground-truth companion to the
        # sample-based probes. Sub-sample very large files (n_subsample
        # measures every Nth frame - hours of full decode become minutes)
        # while keeping the score honest: the sampled frames are still the
        # COMPLETE movie's frames, not the probe windows the CRF search
        # already saw. A verification failure does NOT invalidate the
        # encode (it measured fine at probe time) - it surfaces in the
        # result and the record, never as a hard error.
        output_size = output_file.stat().st_size
        verified_subsample = 10 if output_size > VERIFY_SUBSAMPLE_THRESHOLD_BYTES else None
        try:
            verify_workdir = Path(tempfile.mkdtemp(prefix="shrinkray_verify_"))
            try:
                verified_vmaf = measure_vmaf(
                    output_file,
                    input_file,
                    verify_workdir,
                    crop=crop,
                    hdr=hdr,
                    cancel=cancel,
                    db_file=db_file,
                    subsample=verified_subsample,
                )
            finally:
                shutil.rmtree(verify_workdir, ignore_errors=True)
            log(
                f"Verified '{input_file.name}': full-file VMAF "
                f"{verified_vmaf:.2f}"
                + (f" (sub-sampled 1/{verified_subsample})" if verified_subsample else "")
                + f" vs target {target_vmaf}"
            )
        except Cancelled:
            raise
        except (RuntimeError, ValueError) as e:
            log_error(f"Verification of '{input_file.name}' failed (non-fatal): {e}")
            verified_vmaf = None
            verified_subsample = None

    return QualityResult(
        input=input_file,
        output=output_file,
        success=True,
        target_vmaf=target_vmaf,
        chosen_crf=crf,
        achieved_vmaf=achieved,
        probes=probes,
        verified_vmaf=verified_vmaf,
        verified_subsample=verified_subsample,
        input_size=input_file.stat().st_size,
        output_size=output_file.stat().st_size,
        encoder_preset=encoder_preset,
        probe_encoder_preset=probe_encoder_preset,
        encode_seconds=_time.perf_counter() - run_started,
        probe_seconds=probe_seconds,
        hdr=hdr,
        encoder=effective_encoder,
        tool_versions=_tool_versions(handbrake),
        input_width=snapshot["width"],
        input_height=snapshot["height"],
        input_codec=snapshot["codec"],
        input_duration=snapshot["duration"],
        input_bitrate=snapshot["bitrate"],
        output_bitrate=_output_bitrate(output_file, snapshot["duration"]),
        crf_source=crf_source,
        plan_created_at=plan_created_at,
    )
