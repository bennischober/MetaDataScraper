import csv
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from metadata_scrapper import db
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
from metadata_scrapper.logger import log, log_error, log_warning
from metadata_scrapper.media import get_data

app = typer.Typer(
    help="A short python script to get video meta data.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

OUTPUT_FILE = Path("movies.txt")
CSV_FILE = Path("output.csv")
DB_FILE = Path("metadata.db")


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


def movie_record_from_data(filename: Path, data: dict) -> db.MovieRecord:
    """Converts the get_data output into a database record.

    Args:
        filename (Path): Path to the movie file.
        data (dict): The metadata dictionary from get_data.

    Returns:
        db.MovieRecord: The record for the database.
    """
    stat = filename.stat()
    return db.MovieRecord(
        path=filename,
        mtime=stat.st_mtime,
        size=stat.st_size,
        hash=data.get("hash"),
        name=data["name"],
        category=data["category"],
        duration=data["duration"],
        duration_raw=data["duration_raw"],
        fps=data["fps"],
        codec=data["codec"],
        bitrate=data["bitrate"],
        aspect_ratio=data["aspect_ratio"],
        width=data["dimensions"]["width"],
        height=data["dimensions"]["height"],
        crop=data["crop"],
        hdr=int(data["hdr"]),
        other_codecs=data["other_codecs"],
        audio_languages=data["audio_languages"],
    )


def record_from_cache(row) -> dict:
    """Converts a cached database row into the movie data dictionary used by the output.

    Size and compression rate are recomputed from the row data since they
    are derived values that are not stored in the database.

    Args:
        row (sqlite3.Row): The cached file row.

    Returns:
        dict: The movie data dictionary.
    """
    size = convert_unit(row["size"], SIZE_UNIT.GB)

    # it's not really the compression rate, it's rather size/time
    compression_rate = 0
    if row["duration_raw"] is not None:
        compression_rate = round(size / (row["duration_raw"] / 3600), 2)
    elif row["duration"] is not None:
        compression_rate = round(size / (timestr_to_int(row["duration"]) / 3600), 2)

    # fps comes back as float from the database => '25' instead of '25.0'
    fps = row["fps"]
    if fps is not None and float(fps).is_integer():
        fps = int(fps)

    return {
        "name": row["name"],
        "category": row["category"],
        "duration": row["duration"],
        "duration_raw": row["duration_raw"],
        "fps": fps,
        "codec": row["codec"],
        "bitrate": row["bitrate"],
        "aspect_ratio": row["aspect_ratio"],
        "dimensions": {"width": row["width"], "height": row["height"]},
        "crop": row["crop"],
        "hdr": bool(row["hdr"]),
        "other_codecs": row["other_codecs"],
        "audio_languages": row["audio_languages"],
        "size": size,
        "compression_rate": compression_rate,
    }


def process_movie(
    filename: Path, root: Path, config, compute_hash: bool = False
) -> tuple[dict | None, str | None]:
    """Collects the metadata of a single movie file.

    Args:
        filename (Path): Path to the movie file.
        root (Path): The scan root directory (for the category).
        config (Conf): The config settings.
        compute_hash (bool): Compute a fast content hash of the file.

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

    file_hash = db.compute_hash(filename) if compute_hash else None

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
    ret["hash"] = file_hash

    return ret, None


def scan_movies(
    root: Path,
    config,
    workers: int | None = None,
    connection=None,
    rescan: bool = False,
    compute_hash: bool = False,
) -> tuple[dict, list[Path], list[str]]:
    """Scans the root directory for movies and collects their metadata.

    The movies are processed in parallel (thread pool) since the per-movie
    work consists of ffprobe/cropdetect subprocess calls. Results are
    collected by index => the output order stays deterministic.

    If a database connection is given, unchanged files (mtime + size) are
    taken from the cache instead of being probed again, and all results are
    written back to the database.

    Args:
        root (Path): Root directory to search in.
        config (Conf): The config settings.
        workers (int | None): Number of parallel workers. Defaults to
            min(8, cpu count).
        connection (sqlite3.Connection | None): Database connection for the
            scan cache.
        rescan (bool): Re-probe all files even if they are cached.
        compute_hash (bool): Compute a fast content hash for new/changed files.

    Returns:
        tuple: Movies by category, all found movies and error messages.
    """
    movies_by_category: dict[str, list[dict]] = {}

    # search for movie files with the configured extensions, recursive
    movies = find_movies(root, config.extensions)
    if not movies:
        log_warning(f"No movie files found in '{root}' with extensions {config.extensions}!")
        return movies_by_category, movies, []

    error_messages: list[str | None] = [None] * len(movies)
    results: list[dict | None] = [None] * len(movies)

    # cache lookup => which files can be taken from the database?
    cached: dict[str, object] = {}
    if connection is not None and not rescan:
        cache = db.get_cached_records(connection)
        for index, filename in enumerate(movies):
            row = cache.get(str(filename))
            if row is not None and db.is_unchanged(row, filename):
                results[index] = record_from_cache(row)
                cached[str(filename)] = row

    to_scan = [filename for index, filename in enumerate(movies) if results[index] is None]

    with create_movie_progress() as progress:
        task = progress.add_task("Scanning movies", total=len(movies))
        progress.advance(task, advance=len(cached))
        if cached:
            log(f"{len(cached)} movies taken from the cache.")

        if not to_scan:
            pass  # everything cached
        elif workers == 1:
            # sequential fallback => no thread pool overhead
            for index, filename in enumerate(movies):
                if results[index] is not None:
                    continue
                results[index], error_messages[index] = process_movie(
                    filename, root, config, compute_hash=compute_hash
                )
                progress.advance(task)
        else:
            if workers is None:
                workers = min(8, (os.cpu_count() or 2))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(
                        process_movie, filename, root, config, compute_hash=compute_hash
                    ): index
                    for index, filename in enumerate(movies)
                    if results[index] is None
                }
                for future, index in futures.items():
                    results[index], error_messages[index] = future.result()
                    progress.advance(task)

    # collect in file order => deterministic output
    error_list = [error for error in error_messages if error is not None]
    for result in results:
        if result is None:
            continue
        movies_by_category.setdefault(result["category"], []).append(result)

    # write back to the database
    if connection is not None:
        for index, result in enumerate(results):
            if result is None:
                continue
            record = movie_record_from_data(movies[index], result)
            db.upsert_file(connection, record)
        # remove files that are no longer on disk
        pruned = db.prune_missing(connection, {str(m) for m in movies})
        if pruned:
            log(f"Removed {pruned} missing movies from the database.")

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


def print_table(movies_by_category: dict, total_time, total_size: float):
    """Prints the scanned movies as a rich table to the console.

    Args:
        movies_by_category (dict): Categories as keys and movie lists as values.
        total_time (str | None): Total duration as 'HH:MM:SS' string.
        total_size (float): Total size in GB.
    """
    table = Table(
        title=f"Scanned {sum(len(m) for m in movies_by_category.values())} movies "
        f"in {len(movies_by_category)} categories",
        title_justify="left",
        row_styles=["", "dim"],
    )
    table.add_column("Category", style="cyan", no_wrap=True, max_width=16)
    table.add_column("Name", style="bold", max_width=28, overflow="ellipsis")
    table.add_column("Duration", justify="right", no_wrap=True)
    table.add_column("Size GB", justify="right", no_wrap=True)
    table.add_column("GB/h", justify="right", no_wrap=True)
    table.add_column("Codec", no_wrap=True)
    table.add_column("Res", justify="center", no_wrap=True)
    table.add_column("FPS", justify="right", no_wrap=True)
    table.add_column("Bars", justify="center", no_wrap=True)
    table.add_column("HDR", justify="center", no_wrap=True)

    for category, movies in movies_by_category.items():
        for index, movie in enumerate(movies):
            dimensions = movie["dimensions"]
            table.add_row(
                category if index == 0 else "",
                str(movie["name"]),
                str(movie["duration"]),
                f"{movie['size']:.2f}",
                f"{movie['compression_rate']:.2f}",
                str(movie["codec"]),
                f"{dimensions['width']}x{dimensions['height']}",
                str(movie["fps"]),
                "yes" if movie["crop"] else "",
                "yes" if movie["hdr"] else "",
            )
        # divider between categories
        table.add_section()

    # total row
    table.add_row(
        "",
        "Total",
        str(total_time),
        f"{total_size:.2f}",
        "",
        "",
        "",
        "",
        "",
        "",
        style="bold green",
    )

    console.print(table)


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
    no_table: bool = typer.Option(
        False, "--no-table", help="Do not print the results as table to the console."
    ),
    db_file: Path = typer.Option(
        DB_FILE, "--db", help="Path of the sqlite database file (scan cache/history)."
    ),
    rescan: bool = typer.Option(
        False, "--rescan", help="Re-probe all files even if they are cached."
    ),
    hash_files: bool = typer.Option(
        False, "--hash", help="Compute a fast content hash for new/changed files."
    ),
):
    """Scans the movie folder and writes the metadata to a text file."""
    config = load_config()

    connection = db.connect(db_file)
    try:
        movies_by_category, movies, error_messages = scan_movies(
            path,
            config,
            workers=workers if workers > 0 else None,
            connection=connection,
            rescan=rescan,
            compute_hash=hash_files,
        )
    finally:
        connection.close()
    if movies:
        total_time, total_size = _totals(movies_by_category)
        write_output(movies_by_category, total_time, total_size, config.separator, output_file)
        if not no_table:
            print_table(movies_by_category, total_time, total_size)
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
    quality: bool = typer.Option(
        False,
        "--quality",
        help=(
            "Probe VMAF quality per movie and pick the largest CRF that keeps "
            "perceptual quality (slower, smaller files)."
        ),
    ),
    target_vmaf: float = typer.Option(
        95.0,
        "--target-vmaf",
        min=1.0,
        max=100.0,
        help="VMAF quality floor for --quality mode.",
    ),
    db_file: Path = typer.Option(
        DB_FILE, "--db", help="Path of the sqlite database file (scan cache/history)."
    ),
):
    """Re-encodes the found movies with HandBrake to reduce their file size."""

    config = load_config()
    connection = db.connect(db_file)
    try:
        _, movies, _ = scan_movies(path, config, connection=connection)
        _record_encodes(
            connection, movies, output_dir, config, dry_run, overwrite, quality, target_vmaf
        )
    finally:
        connection.close()


def _record_encodes(
    connection, movies, output_dir, config, dry_run, overwrite, quality, target_vmaf
):
    """Runs the encoding and records the results in the database.

    Args:
        connection: The database connection.
        movies (list[Path]): The movie files to encode.
        output_dir (Path): Directory for the encoded files.
        config (Conf): The config settings.
        dry_run (bool): Only show what would be encoded.
        overwrite (bool): Whether existing output files may be replaced.
        quality (bool): Use VMAF-driven quality mode.
        target_vmaf (float): VMAF quality floor for quality mode.
    """
    from metadata_scrapper.encode import encode_movies
    from metadata_scrapper.quality import encode_with_quality

    # already encoded? (unless --overwrite)
    encoded_paths: set[str] = set()
    if not overwrite and not dry_run:
        for movie in movies:
            file_row = db.get_file_by_path(connection, movie)
            if file_row is not None and db.get_encodes_for_file(connection, file_row["id"]):
                encoded_paths.add(str(movie))

    if not dry_run and quality:
        try:
            for movie in movies:
                if str(movie) in encoded_paths:
                    log(f"Skipping '{movie.name}': already encoded (use --overwrite).")
                    continue
                result = encode_with_quality(
                    movie,
                    output_dir / movie.name,
                    target_vmaf=target_vmaf,
                    encoder=config.handbrake_encoder,
                )
                if result.success:
                    saved = result.saved_percent
                    change = f"{saved}% saved" if saved >= 0 else f"{abs(saved)}% larger"
                    console.print(
                        f"[green]Encoded '{movie.name}': "
                        f"{result.input_size / (1024**3):.2f} GB -> "
                        f"{result.output_size / (1024**3):.2f} GB ({change}) "
                        f"at CRF {result.chosen_crf}, VMAF {result.achieved_vmaf}[/green]"
                    )
                    _save_encode(
                        connection,
                        movie,
                        f"VMAF>{target_vmaf}",
                        config.handbrake_encoder,
                        result.input_size,
                        result.output_size,
                    )
                else:
                    log_error(f"Encoding '{movie.name}' failed: {result.error}")
        except (RuntimeError, FileNotFoundError) as e:
            log_error(str(e))
            raise typer.Exit(1) from e
        return

    results: list = []
    try:
        results = encode_movies(
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

    # record the encodes of this run in the database
    for encode_result in results:
        if not encode_result.success:
            continue
        _save_encode(
            connection,
            encode_result.input,
            config.handbrake_preset,
            config.handbrake_encoder,
            encode_result.input_size,
            encode_result.output_size,
        )


def _save_encode(
    connection, movie: Path, preset: str, encoder: str, input_size: int, output_size: int
):
    """Stores an encode record for the given movie in the database.

    Args:
        connection: The database connection.
        movie (Path): The input movie file.
        preset (str): The preset (or quality target) used.
        encoder (str): The encoder used.
        input_size (int): Input size in bytes.
        output_size (int): Output size in bytes.
    """
    file_row = db.get_file_by_path(connection, movie)
    if file_row is None:
        # file not in the scan cache => store it first
        record = db.MovieRecord(
            path=movie,
            mtime=movie.stat().st_mtime,
            size=movie.stat().st_size,
            name=movie.stem,
            category=movie.parent.name,
        )
        file_id = db.upsert_file(connection, record)
    else:
        file_id = file_row["id"]

    saved_percent = round((1 - output_size / input_size) * 100, 2) if input_size else 0.0
    db.add_encode(connection, file_id, preset, encoder, input_size, output_size, saved_percent)


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
