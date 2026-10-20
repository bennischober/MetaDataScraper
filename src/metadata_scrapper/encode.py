import subprocess
from dataclasses import dataclass
from pathlib import Path

from metadata_scrapper.logger import log_error

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
    import shutil

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


def build_command(input_file: Path, output_file: Path, preset: str, encoder: str) -> list[str]:
    """Builds the HandBrakeCLI command for re-encoding a file.

    Args:
        input_file (Path): The file to re-encode.
        output_file (Path): The target file.
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (str): The video encoder, e.g. 'x265'.

    Returns:
        list[str]: The command as argument list.
    """
    return [
        HANDBRAKE_BINARY,
        "--input",
        str(input_file),
        "--output",
        str(output_file),
        "--preset",
        preset,
        "--encoder",
        encoder,
        # keep all audio and subtitle tracks, passthrough audio => no quality loss
        "--all-audio",
        "--all-subtitles",
        "--audio",
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

    if output_file.exists() and not overwrite:
        raise FileExistsError(f"Output file '{output_file}' already exists (use overwrite=True)!")

    if find_handbrake() is None:
        raise RuntimeError(
            f"'{HANDBRAKE_BINARY}' not found! Install HandBrake CLI "
            "(https://handbrake.fr/docs/en/latest/cli/cli-options.html)"
        )

    command = build_command(input_file, output_file, preset, encoder)
    try:
        proc = subprocess.run(command, capture_output=True)
    except (OSError, subprocess.SubprocessError) as e:
        return EncodeResult(input=input_file, output=output_file, success=False, error=str(e))

    if proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        return EncodeResult(input=input_file, output=output_file, success=False, error=stderr)

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
) -> list[EncodeResult]:
    """Re-encodes multiple movie files into an output directory.

    Args:
        movies (list[Path]): The movie files to re-encode.
        output_dir (Path): Directory for the encoded files (created if missing).
        preset (str): The HandBrake preset, e.g. 'Fast 1080p30'.
        encoder (str): The video encoder, e.g. 'x265'.
        dry_run (bool): If True, only show what would be encoded.

    Returns:
        list[EncodeResult]: The results of the encodes.
    """
    results: list[EncodeResult] = []

    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    for movie in movies:
        output_file = output_dir / movie.name
        if dry_run:
            from metadata_scrapper.logger import log

            log(f"[dry-run] Would encode '{movie}' -> '{output_file}'")
            continue

        result = encode_file(movie, output_file)
        if result.success:
            from metadata_scrapper.logger import log

            log(
                f"Encoded '{movie.name}': {result.input_size / (1024**3):.2f} GB -> "
                f"{result.output_size / (1024**3):.2f} GB "
                f"({result.saved_percent}% saved)"
            )
        else:
            log_error(f"Encoding '{movie.name}' failed: {result.error}")
        results.append(result)

    return results
