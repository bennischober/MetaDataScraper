import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.progress import Progress

from shrinkray.logger import log, log_error

HANDBRAKE_BINARY = "HandBrakeCLI"


@dataclass
class EncodeResult:
    """Result of a HandBrake encode.

    Attributes:
        input (Path): The input file.
        output (Path): The output file.
        success (bool): True if the encode succeeded.
        input_size (int): Size of the input file in bytes.
        output_size (int): Size of the output file in bytes.
        error (str | None): The error message if the encode failed.
    """

    input: Path
    output: Path
    success: bool
    input_size: int = 0
    output_size: int = 0
    error: str | None = None
    input_width: int | None = None
    input_height: int | None = None
    input_codec: str | None = None
    input_duration: float | None = None

    @property
    def saved_percent(self) -> float:
        """Percentage of disk space saved by the encode."""
        if self.input_size <= 0 or self.output_size <= 0:
            return 0.0
        return round((1 - self.output_size / self.input_size) * 100, 2)


def find_handbrake() -> str | None:
    """Finds the HandBrakeCLI binary on this system.

    Returns:
        str | None: The path to the binary, or None if it was not found.
    """
    binary = shutil.which(HANDBRAKE_BINARY)
    if binary:
        return binary

    # common windows install locations
    for path in (
        r"C:\Program Files\HandBrake\HandBrakeCLI.exe",
        r"C:\Program Files (x86)\HandBrake\HandBrakeCLI.exe",
    ):
        if Path(path).is_file():
            return path
    return None


def probe_duration_seconds(path: Path) -> float | None:
    """Reads the duration of a media file via ffprobe.

    Args:
        path (Path): The media file.

    Returns:
        float | None: The duration in seconds, or None if it could not be
            determined (e.g. missing ffprobe, truncated/unfinalized file).
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
        )
    except OSError:
        # ffprobe not installed (e.g. CI) => duration unknown
        return None
    if proc.returncode != 0:
        return None
    try:
        duration = float(json.loads(proc.stdout.decode())["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if duration <= 0:
        return None
    return duration


# files shorter than this are not treated as encodable movies (e.g. probe
# samples, trailers) => skipped by encode_movies; real featurettes can be
# a few minutes long, so this stays well below that
MIN_ENCODE_SECONDS = 90.0


def durations_match(input_file: Path, output_file: Path) -> bool:
    """Checks that the encoded output has (almost) the input duration.

    HandBrake may exit with 0 even for an interrupted encode that only wrote
    part of the file. A truncated output is shorter or has no readable
    duration at all.

    Args:
        input_file (Path): The source file.
        output_file (Path): The encoded file.

    Returns:
        bool: True if the durations match within a small tolerance.
    """
    source = probe_duration_seconds(input_file)
    encoded = probe_duration_seconds(output_file)
    if source is None and encoded is None:
        # cannot verify at all (e.g. ffprobe missing) => do not report failure
        return True
    if encoded is None:
        # source readable but output unreadable => genuinely broken
        return False
    if source is None:
        return True
    # tolerate frame rounding; 1% or 2s, whichever is larger
    tolerance = max(2.0, source * 0.01)
    return abs(source - encoded) <= tolerance


def build_command(
    binary: str,
    input_file: Path,
    output_file: Path,
    preset: str,
    encoder: str,
    encoder_threads: int | None = None,
) -> list[str]:
    """Builds the HandBrakeCLI command for re-encoding a file.

    Args:
        binary (str): Path to the HandBrakeCLI binary.
        input_file (Path): The file to re-encode.
        output_file (Path): The target file.
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (str): The video encoder, e.g. 'x265'.
        encoder_threads (int | None): Thread limit for the encoder
            (for parallel encoding across multiple movies). None = no limit.

    Returns:
        list[str]: The command as argument list.
    """
    command = [
        binary,
        "--input",
        str(input_file),
        "--output",
        str(output_file),
        "--preset",
        preset,
        "--encoder",
        encoder,
        # keep all audio and subtitle tracks, copy audio => no quality loss
        "--all-audio",
        "--all-subtitles",
        "--aencoder",
        "copy",
        "--optimize",  # web optimized => mp4/mkv file can start streaming
    ]
    if encoder_threads is not None:
        # HandBrakeCLI has no thread flag; x264/x265 take it via --encopts
        command += ["--encopts", f"threads={encoder_threads}"]
    return command


def _encode_with_command(
    input_file: Path,
    output_file: Path,
    command: list[str],
    progress=None,
) -> EncodeResult:
    """Runs one HandBrake command and verifies its output.

    With a progress callback, HandBrake's stdout is streamed and its
    percentage lines ('Encoding: task 1 of 1, 42.31 %') are reported live
    as {'stage': 'encode', 'percent': 42.31, 'movie': name}.

    Args:
        input_file (Path): The source file.
        output_file (Path): The target file.
        command (list[str]): The full HandBrakeCLI command.
        progress (callable | None): Callback progress(dict) for live updates.

    Returns:
        EncodeResult: The result of the encode.
    """
    stderr = ""
    returncode: int
    stderr_bytes: bytes | None = None
    try:
        if progress is None:
            proc = subprocess.run(command, capture_output=True)
            returncode = proc.returncode
            stderr_bytes = proc.stderr
        else:
            popen = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert popen.stdout is not None  # stdout=PIPE guarantees it
            for raw in iter(popen.stdout.readline, b""):
                line = raw.decode(errors="replace").strip()
                match = re.search(r"(\d{1,3}\.\d{2})\s*%", line)
                if match:
                    progress(
                        {
                            "stage": "encode",
                            "percent": float(match.group(1)),
                            "movie": input_file.name,
                        }
                    )
            popen.stdout.close()
            _, stderr_raw = popen.communicate()
            stderr_bytes = stderr_raw
            returncode = popen.returncode
    except (OSError, subprocess.SubprocessError) as e:
        return EncodeResult(input=input_file, output=output_file, success=False, error=str(e))

    stderr = (stderr_bytes or b"").decode(errors="replace").strip()
    if returncode != 0:
        return EncodeResult(input=input_file, output=output_file, success=False, error=stderr)

    # HandBrake may exit with 0 even if the encode failed => the output file
    # must exist, be non-empty and have the full duration
    if not output_file.is_file() or output_file.stat().st_size == 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        return EncodeResult(
            input=input_file,
            output=output_file,
            success=False,
            error=stderr or "no output file was written",
        )
    if not durations_match(input_file, output_file):
        return EncodeResult(
            input=input_file,
            output=output_file,
            success=False,
            error="output is truncated (duration mismatch)",
        )

    # encode succeeded => collect sizes + the source snapshot
    snapshot = _source_snapshot(input_file)
    return EncodeResult(
        input=input_file,
        output=output_file,
        success=True,
        input_size=input_file.stat().st_size,
        output_size=output_file.stat().st_size,
        input_width=snapshot["width"],
        input_height=snapshot["height"],
        input_codec=snapshot["codec"],
        input_duration=snapshot["duration"],
    )


def _source_snapshot(movie: Path) -> dict:
    """Reads the source file's resolution/codec/duration for encode records.

    The encode record must be self-contained: the files table row can change
    on a later re-scan, so the benchmark data snapshots what was true at
    encode time.

    Args:
        movie (Path): The source file.

    Returns:
        dict: width/height/codec/duration (None where unknown).
    """
    duration = probe_duration_seconds(movie)
    width = height = None
    codec = None
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
            timeout=30,
        )
        if proc.returncode == 0:
            streams = json.loads(proc.stdout.decode()).get("streams", [])
            video: dict = next((s for s in streams if s.get("codec_type") == "video"), {})
            width = video.get("width")
            height = video.get("height")
            codec = video.get("codec_name")
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {"width": width, "height": height, "codec": codec, "duration": duration}


def encode_file(
    input_file: Path,
    output_file: Path,
    preset: str = "Fast 1080p30",
    encoder: str = "x265",
    overwrite: bool = False,
    progress=None,
) -> EncodeResult:
    """Re-encodes a movie file with HandBrakeCLI to reduce its file size.

    The HandBrakeCLI binary must be installed and findable (see find_handbrake).

    Args:
        input_file (Path): The file to re-encode.
        output_file (Path): The target file (must not exist, or overwrite=True).
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (e.g. str): The video encoder, e.g. 'x265'.
        overwrite (bool): Whether an existing output file may be replaced.
        progress (callable | None): Callback progress(dict) for live updates.

    Returns:
        EncodeResult: The result of the encode.
    """
    if not input_file.is_file():
        raise FileNotFoundError(f"Input file '{input_file}' does not exist!")

    handbrake = find_handbrake()
    if handbrake is None:
        raise RuntimeError(
            f"'{HANDBRAKE_BINARY}' not found! Install HandBrake CLI "
            "(https://handbrake.fr/docs/en/latest/cli/cli-options.html)"
        )

    command = build_command(handbrake, input_file, output_file, preset, encoder)
    return _encode_with_command(input_file, output_file, command, progress=progress)


def encode_movies(
    movies: list[Path],
    output_dir: Path,
    preset: str = "Fast 1080p30",
    encoder: str = "x265",
    dry_run: bool = False,
    overwrite: bool = False,
    workers: int | None = None,
    progress=None,
) -> list[EncodeResult]:
    """Re-encodes multiple movie files into an output directory.

    With workers > 1, several HandBrake processes run concurrently, each
    capped to a share of the CPU threads so the encodes do not oversubscribe
    the machine. Results are collected by index => deterministic order.

    Args:
        movies (list[Path]): The movie files to re-encode.
        output_dir (Path): Directory for the encoded files (created if missing).
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (str): The video encoder, e.g. 'x265'.
        dry_run (bool): If True, only show what would be encoded.
        overwrite (bool): Whether existing output files may be replaced.
        workers (int | None): Number of concurrent encodes (1 = sequential,
            None = automatic: 1, since a single x265 encode already scales
            across all cores).
        progress (callable | None): Callback progress(dict) for live updates
            of the running encode's percentage.

    Returns:
        list[EncodeResult]: The results of the encodes.
    """
    results: list[EncodeResult] = []

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    if dry_run:
        for movie in movies:
            output_file = output_dir / movie.name
            log(f"[dry-run] Would encode '{movie}' -> '{output_file}'")
        return results

    if workers is None:
        # a single x265 encode already uses all cores efficiently; parallel
        # encodes only help when the per-encode thread count is capped lower
        workers = 1
    workers = max(1, workers)

    # filter skipped movies first (deterministic, before any encoding)
    pending: list[tuple[Path, Path]] = []
    for movie in movies:
        duration = probe_duration_seconds(movie)
        if duration is not None and duration < MIN_ENCODE_SECONDS:
            log(
                f"Skipping '{movie.name}': duration {duration:.0f}s is below "
                f"the {MIN_ENCODE_SECONDS:.0f}s minimum (not a movie?)."
            )
            continue
        output_file = output_dir / movie.name
        if output_file.exists() and not overwrite:
            log(f"Skipping '{movie.name}': output file already exists.")
            continue
        pending.append((movie, output_file))

    if not pending:
        return results

    # longest job first: big movies start early so the tail of the run is
    # short jobs, not an idle half machine next to one capped long encode
    pending.sort(key=lambda pair: -(probe_duration_seconds(pair[0]) or 0))

    cpu = os.cpu_count() or 8

    def run_one(movie: Path, output_file: Path, threads: int | None) -> EncodeResult:
        handbrake = find_handbrake()
        if handbrake is None:
            raise RuntimeError(
                f"'{HANDBRAKE_BINARY}' not found! Install HandBrake CLI "
                "(https://handbrake.fr/docs/en/latest/cli/cli-options.html)"
            )
        command = build_command(
            handbrake,
            movie,
            output_file,
            preset,
            encoder,
            encoder_threads=threads,
        )
        return _encode_with_command(movie, output_file, command, progress=progress)

    with Progress() as progress:
        task = progress.add_task("Encoding movies", total=len(pending))
        if workers == 1:
            for movie, output_file in pending:
                result = run_one(movie, output_file, None)
                _report_encode(progress, result)
                results.append(result)
                progress.advance(task)
        else:
            # work-queue scheduling: every started encode holds a slot with a
            # fair share of the CPU; when a slot frees, the next movie starts
            # with the share of ALL free slots (a lone long movie reclaims the
            # full machine as the short ones finish)
            from concurrent.futures import ThreadPoolExecutor
            from threading import Lock

            lock = Lock()
            running: list[int] = []  # thread budgets of running encodes

            def slot_budget() -> int:
                with lock:
                    free = max(1, workers - len(running))
                    # shares are equal among the encodes that would run
                    return max(1, cpu // max(1, len(running) + free) * free)

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = []
                index = 0
                while index < len(pending) or running:
                    with lock:
                        free = workers - len(running)
                    if free > 0 and index < len(pending):
                        movie, output_file = pending[index]
                        index += 1
                        budget = slot_budget()
                        with lock:
                            running.append(budget)
                        future = executor.submit(run_one, movie, output_file, budget)

                        def done(fut, budget=budget):
                            with lock:
                                running.remove(budget)
                            result = fut.result()
                            _report_encode(progress, result)
                            results.append(result)
                            progress.advance(task)

                        future.add_done_callback(done)
                        futures.append(future)
                    else:
                        import time

                        time.sleep(0.5)
                for future in futures:
                    future.result()

    return results


def _report_encode(progress: Progress, result: EncodeResult):
    """Logs one finished encode to the progress console."""
    if result.success:
        saved = result.saved_percent
        change = f"{saved}% saved" if saved >= 0 else f"{abs(saved)}% larger"
        progress.console.print(
            f"Encoded '{result.input.name}': {result.input_size / (1024**3):.2f} GB -> "
            f"{result.output_size / (1024**3):.2f} GB ({change})"
        )
    else:
        log_error(f"Encoding '{result.input.name}' failed: {result.error}")
