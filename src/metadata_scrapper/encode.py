import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.progress import Progress

from metadata_scrapper.logger import log, log_error

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
            determined (e.g. truncated/unfinalized file).
    """
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
    if proc.returncode != 0:
        return None
    try:
        return float(json.loads(proc.stdout.decode())["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


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
    if source is None or encoded is None:
        return False
    # tolerate frame rounding; 1% or 2s, whichever is larger
    tolerance = max(2.0, source * 0.01)
    return abs(source - encoded) <= tolerance


def build_command(
    binary: str, input_file: Path, output_file: Path, preset: str, encoder: str
) -> list[str]:
    """Builds the HandBrakeCLI command for re-encoding a file.

    Args:
        binary (str): Path to the HandBrakeCLI binary.
        input_file (Path): The file to re-encode.
        output_file (Path): The target file.
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (str): The video encoder, e.g. 'x265'.

    Returns:
        list[str]: The command as argument list.
    """
    return [
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


def encode_file(
    input_file: Path,
    output_file: Path,
    preset: str = "Fast 1080p30",
    encoder: str = "x265",
    overwrite: bool = False,
) -> EncodeResult:
    """Re-encodes a movie file with HandBrakeCLI to reduce its file size.

    The HandBrakeCLI binary must be installed and findable (see find_handbrake).

    Args:
        input_file (Path): The file to re-encode.
        output_file (Path): The target file (must not exist, or overwrite=True).
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (e.g. str): The video encoder, e.g. 'x265'.
        overwrite (bool): Whether an existing output file may be replaced.

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
    try:
        proc = subprocess.run(command, capture_output=True)
    except (OSError, subprocess.SubprocessError) as e:
        return EncodeResult(input=input_file, output=output_file, success=False, error=str(e))

    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
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

    # encode succeeded => collect sizes
    input_size = input_file.stat().st_size
    output_size = output_file.stat().st_size if output_file.is_file() else 0
    return EncodeResult(
        input=input_file,
        output=output_file,
        success=True,
        input_size=input_size,
        output_size=output_size,
    )


def encode_movies(
    movies: list[Path],
    output_dir: Path,
    preset: str = "Fast 1080p30",
    encoder: str = "x265",
    dry_run: bool = False,
    overwrite: bool = False,
) -> list[EncodeResult]:
    """Re-encodes multiple movie files into an output directory.

    Args:
        movies (list[Path]): The movie files to re-encode.
        output_dir (Path): Directory for the encoded files (created if missing).
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (str): The video encoder, e.g. 'x265'.
        dry_run (bool): If True, only show what would be encoded.
        overwrite (bool): Whether existing output files may be replaced.

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

    with Progress() as progress:
        task = progress.add_task("Encoding movies", total=len(movies))
        for movie in movies:
            output_file = output_dir / movie.name
            if output_file.exists() and not overwrite:
                log(f"Skipping '{movie.name}': output file already exists.")
                progress.advance(task)
                continue
            result = encode_file(movie, output_file)
            if result.success:
                saved = result.saved_percent
                change = f"{saved}% saved" if saved >= 0 else f"{abs(saved)}% larger"
                progress.console.print(
                    f"Encoded '{movie.name}': {result.input_size / (1024**3):.2f} GB -> "
                    f"{result.output_size / (1024**3):.2f} GB ({change})"
                )
            else:
                log_error(f"Encoding '{movie.name}' failed: {result.error}")
            results.append(result)
            progress.advance(task)

    return results
