import csv
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer
from rich.console import Console

from metadata_scrapper.config import load_config
from metadata_scrapper.helpers import (
    SIZE_UNIT,
    calc_time,
    check_black_bars,
    convert_unit,
    create_movie_progress,
    generate_string,
    get_other_codecs,
    sanitize_number,
    timestr_to_int,
)
from metadata_scrapper.logger import log_error, log_warning
from metadata_scrapper.media import get_data

app = typer.Typer(
    help="A short python script to get video meta data.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

OUTPUT_FILE = "movies.txt"
CSV_FILE = "output.csv"


def get_name(filename: Path) -> str:
    """Determines the movie name from the file name.

    Multiple movies in one folder get their names from the file,
    otherwise the parent folder name is used (e.g. 'Movie (2020)' folders).

    Args:
        filename (Path): Path to the movie file.

    Returns:
        str: The movie name without extension.
    """
    if "title" in filename.name or "_t0" in filename.name:
        return filename.parent.name
    return filename.stem


def get_category(filename: Path, root: Path) -> str:
    """Determines the movie category from the folder structure.

    The category is the folder above the movie folder (relative to the root),
    e.g. 'Movies/Matrix/movie.mkv' => 'Movies'. If the file is not nested
    deep enough, 'Uncategorized' is used.

    Args:
        filename (Path): Path to the movie file.
        root (Path): The scan root directory.

    Returns:
        str: The category name.
    """
    try:
        relative = filename.parent.relative_to(root)
    except ValueError:
        # file is not inside the root (should not happen) => no category
        return "Uncategorized"

    parts = relative.parts
    if len(parts) == 0:
        # movie file directly in the root
        return "Uncategorized"
    if len(parts) == 1:
        # movie file in a folder directly under the root => use that folder
        return parts[0]
    return parts[0]


def find_movies(root: Path, extensions: list[str]) -> list[Path]:
    """Finds all movie files with the given extensions recursively.

    Args:
        root (Path): Root directory to search in.
        extensions (list[str]): Extensions to search for, e.g. ['.mkv', '.mp4'].

    Returns:
        list[Path]: List of movie files.
    """
    movies: list[Path] = []
    for extension in extensions:
        extension = extension.lower().lstrip(".")
        # glob is case sensitive on linux => check both cases
        movies.extend(root.glob(f"**/*.{extension}"))
        movies.extend(root.glob(f"**/*.{extension.upper()}"))
    # on windows glob is case insensitive => the same file is found twice
    return list(dict.fromkeys(movies))


def process_movie(filename: Path, root: Path, config) -> tuple[dict | None, str | None]:
    """Collects the metadata of a single movie file.

    Args:
        filename (Path): Path to the movie file.
        root (Path): The scan root directory (for the category).
        config (Conf): The config settings.

    Returns:
        tuple: The movie data dictionary (or None on error) and an error
            message (or None on success).
    """
    name = get_name(filename)
    category = get_category(filename, root)
    try:
        ret = get_data(filename, config)
    except Exception as e:
        # move errors to the caller and show them at the end
        return None, f"A reading error occured in '{name}' at '{filename}'. Exception: {e}"

    size = convert_unit(os.path.getsize(filename), SIZE_UNIT.GB)

    # it's not really the compression rate, it's rather size/time
    compression_rate = 0
    if ret["duration_raw"] is not None:
        compression_rate = round(size / (ret["duration_raw"] / 3600), 2)
    elif ret["duration"] is not None:
        compression_rate = round(size / (timestr_to_int(ret["duration"]) / 3600), 2)

    # check for cropping
    ret["crop"] = check_black_bars(
        filename,
        ret["dimensions"],
        min_width=config.black_bar_min_width,
        min_height=config.black_bar_min_height,
    )

    # add additional data to dictionary
    ret["name"] = name
    ret["category"] = category
    ret["size"] = size
    ret["compression_rate"] = compression_rate  # is SIZE/TIME
    ret["other_codecs"] = get_other_codecs(ret["other_codecs"])

    return ret, None


def scan_movies(
    root: Path, config, workers: int | None = None
) -> tuple[dict, list[Path], list[str]]:
    """Scans the root directory for movies and collects their metadata.

    The movies are processed in parallel (thread pool) since the per-movie
    work consists of ffprobe/cropdetect subprocess calls. Results are
    collected by index => the output order stays deterministic.

    Args:
        root (Path): Root directory to search in.
        config (Conf): The config settings.
        workers (int | None): Number of parallel workers. Defaults to
            min(8, cpu count).

    Returns:
        tuple: Movies by category, all found movies and error messages.
    """
    movies_by_category: dict[str, list[dict]] = {}

    # search for movie files with the configured extensions, recursive
    movies = find_movies(root, config.extensions)
    if not movies:
        log_warning(f"No movie files found in '{root}' with extensions {config.extensions}!")
        return movies_by_category, movies, []

    error_messages = [None] * len(movies)
    results: list[dict | None] = [None] * len(movies)

    with create_movie_progress() as progress:
        task = progress.add_task("Scanning movies", total=len(movies))

        if workers == 1:
            # sequential fallback => no thread pool overhead
            for index, filename in enumerate(movies):
                results[index], error_messages[index] = process_movie(filename, root, config)
                progress.advance(task)
        else:
            if workers is None:
                workers = min(8, (os.cpu_count() or 2))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(process_movie, filename, root, config) for filename in movies
                ]
                for index, future in enumerate(futures):
                    results[index], error_messages[index] = future.result()
                    progress.advance(task)

    # collect in file order => deterministic output
    error_list = [error for error in error_messages if error is not None]
    for result in results:
        if result is None:
            continue
        movies_by_category.setdefault(result["category"], []).append(result)

    return movies_by_category, movies, error_list


def write_output(
    movies_by_category: dict,
    total_time,
    total_size,
    separator: str,
    output_file: Path = OUTPUT_FILE,
):
    """Writes the processed data to the output file.

    Args:
        movies_by_category (dict): Categories as keys and movie lists as values.
        total_time (str | None): Total duration as 'HH:MM:SS' string.
        total_size (float): Total size in GB.
        separator (str): The separator between the columns.
        output_file (Path): Path of the output file.
    """
    with open(output_file, "w+", encoding="UTF8") as text:
        # write csv / file header
        header = [
            "CATEGORY",
            "NAME",
            "DURATION",
            "SIZE",
            "CODEC",
            "BITRATE",
            "ASPECT RATIO",
            "RESOLUTION",
            "GB/h",
            "FPS",
            "BARS",
            "HDR",
            "OTHER CODECS",
            "AUDIO LANGUAGES",
        ]
        text.write(separator.join(header) + "\n")

        # iterate through parent dictionary
        for movies in movies_by_category.values():
            # write value for category / index in dictionary
            rows = ""

            for value in movies:
                # only show the category once per category block
                use_cat = value is movies[0]
                rows += generate_string(value, separator, use_cat)

            text.write(rows)

        # write total duration and size
        total = ["Total", str(total_time), sanitize_number(total_size)]
        total.extend([""] * (len(header) - len(total)))
        text.write(separator.join(total) + "\n")


def write_csv(separator: str, output_file: Path = OUTPUT_FILE, csv_file: Path = CSV_FILE):
    """Converts the output file into a real csv file.

    Args:
        separator (str): The separator between the columns.
        output_file (Path): Path of the txt input file.
        csv_file (Path): Path of the csv output file.
    """
    with open(output_file, encoding="utf-8") as in_file:
        lines = [line.rstrip("\n").split(separator) for line in in_file if line.strip()]
        with open(csv_file, "w+", newline="", encoding="iso-8859-1") as out_file:
            writer = csv.writer(out_file, delimiter=";", quotechar='"', quoting=csv.QUOTE_MINIMAL)
            writer.writerows(lines)


def print_summary(movies_by_category: dict, total_time, total_size: float):
    """Prints a summary of the scanned movies to the console.

    Args:
        movies_by_category (dict): Categories as keys and movie lists as values.
        total_time (str | None): Total duration as 'HH:MM:SS' string.
        total_size (float): Total size in GB.
    """
    movie_count = sum(len(movies) for movies in movies_by_category.values())
    console.print(
        f"[green]Scanned [bold]{movie_count}[/bold] movies "
        f"in [bold]{len(movies_by_category)}[/bold] categories."
    )
    console.print(
        f"Total duration: [bold]{total_time}[/bold], total size: [bold]{total_size:.2f} GB[/bold]"
    )


@app.command()
def scan(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Root path of the movie folder, e.g. D:\\Movies",
    ),
    csv_output: bool = typer.Option(
        False, "--csv", "-csv", help="Also write the data as csv file."
    ),
    output_file: Path = typer.Option(
        OUTPUT_FILE, "--output", "-o", help="Path of the output file."
    ),
    workers: int = typer.Option(
        0,
        "--workers",
        "-w",
        min=0,
        help="Number of parallel workers (0 = automatic, 1 = sequential).",
    ),
):
    """Scans the movie folder and writes the metadata to a text file."""
    config = load_config()

    movies_by_category, movies, error_messages = scan_movies(
        path, config, workers=workers if workers > 0 else None
    )
    if movies:
        total_time, total_size = _totals(movies_by_category)
        write_output(movies_by_category, total_time, total_size, config.separator, output_file)
        print_summary(movies_by_category, total_time, total_size)

        if csv_output:
            csv_file = output_file.with_suffix(".csv")
            write_csv(config.separator, output_file, csv_file)
            console.print(f"Written '{csv_file}'.")

    # show errors
    for er in error_messages:
        log_error(er)


@app.command()
def encode(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Root path of the movie folder, e.g. D:\\Movies",
    ),
    output_dir: Path = typer.Argument(..., help="Directory for the encoded files."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be encoded without encoding."
    ),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Re-encode files even if the output already exists."
    ),
):
    """Re-encodes the found movies with HandBrake to reduce their file size."""
    from metadata_scrapper.encode import encode_movies

    config = load_config()
    _, movies, _ = scan_movies(path, config)

    try:
        encode_movies(
            movies,
            output_dir,
            preset=config.handbrake_preset,
            encoder=config.handbrake_encoder,
            dry_run=dry_run,
            overwrite=overwrite,
        )
    except (RuntimeError, FileNotFoundError) as e:
        log_error(str(e))
        raise typer.Exit(1) from e


def _totals(movies_by_category: dict) -> tuple:
    """Calculates the total duration and size of the scanned movies.

    Args:
        movies_by_category (dict): Categories as keys and movie lists as values.

    Returns:
        tuple: Total duration as 'HH:MM:SS' string and total size in GB.
    """
    total_time = None
    total_size = 0
    for movies in movies_by_category.values():
        for value in movies:
            if value["duration"] is not None:
                total_time = calc_time(total_time, value["duration"])
            total_size += value["size"]
    return total_time, total_size


if __name__ == "__main__":
    app()
