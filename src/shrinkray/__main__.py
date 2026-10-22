"""The command line interface of MetaDataScraper.

All engine logic lives in dedicated modules (scanner, encode, quality,
service, watch, report, db) — this module only defines the commands, their
options and the console presentation.
"""

import csv
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from shrinkray import db
from shrinkray.config import load_config
from shrinkray.encode import cap_threads
from shrinkray.helpers import calc_time, generate_string, sanitize_number
from shrinkray.logger import log, log_error
from shrinkray.report import generate_report
from shrinkray.scanner import find_movies, get_category, get_name, scan_movies
from shrinkray.service import (
    finished_movies,
    record_fixed_results,
    record_quality_results,
)

app = typer.Typer(
    help="A short python script to get video meta data.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
console = Console()

OUTPUT_FILE = Path("movies.txt")
CSV_FILE = Path("output.csv")
DB_FILE = Path("metadata.db")

# re-exports for backwards compatibility (tests and third parties import
# these from __main__)
__all__ = [
    "app",
    "find_movies",
    "get_category",
    "get_name",
    "print_summary",
    "print_table",
    "scan_movies",
    "write_csv",
    "write_output",
]


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


@app.command("app")
def app_command(
    db_file: Path = typer.Option(
        DB_FILE, "--db", help="Path of the sqlite database file (scan cache/history)."
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8420, "--port", min=1, max=65535, help="Bind port."),
    root: list[Path] = typer.Option(
        [],
        "--root",
        help="Folder the API may operate on (repeatable). Defaults to all "
        "available drive roots (the app is localhost-only).",
    ),
):
    """Runs the resident app: job queue, watch loop and the local HTTP API.

    The app owns long-running work; terminal and web UI are just consumers
    of the same engine and database. Stop with Ctrl+C.
    """
    from shrinkray.app import run_app

    # empty list = run_app computes its default (all drive roots); only an
    # explicit --root narrows the allowlist down
    roots = [Path(r) for r in root] or None
    run_app(db_file, host=host, port=port, allowed_roots=roots)


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
    rescan: bool = typer.Option(
        False, "--rescan", help="Re-probe all files even if they are cached."
    ),
    hash_files: bool = typer.Option(
        False, "--hash", help="Compute a fast content hash for new/changed files."
    ),
    db_file: Path = typer.Option(
        DB_FILE, "--db", help="Path of the sqlite database file (scan cache/history)."
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
    remove_bars: bool = typer.Option(
        False,
        "--remove-bars",
        help=(
            "Quality mode: detect stable baked-in black bars (letterbox) and "
            "crop them away (irreversible, opt-in)."
        ),
    ),
    workers: int = typer.Option(
        0,
        "--workers",
        "-w",
        min=0,
        help=(
            "Number of concurrent encodes (fixed mode only; 0 = automatic, "
            "1 = sequential). Each encode gets a share of the CPU threads."
        ),
    ),
    threads: int = typer.Option(
        0,
        "--threads",
        min=0,
        help=(
            "Per-encode thread cap (0 = all cores). Keeps cores free for "
            "a media server or desktop work during long encodes."
        ),
    ),
    db_file: Path = typer.Option(
        DB_FILE, "--db", help="Path of the sqlite database file (scan cache/history)."
    ),
):
    """Re-encodes the found movies with HandBrake to reduce their file size."""

    config = load_config()
    connection = db.connect(db_file)
    try:
        # the output directory must never be a scan target (previous outputs
        # would be re-encoded/re-probed)
        _, movies, _ = scan_movies(path, config, connection=connection, exclude=output_dir)
        _record_encodes(
            connection,
            movies,
            output_dir,
            config,
            dry_run,
            overwrite,
            quality,
            target_vmaf,
            remove_bars,
            workers,
            threads,
            db_file,
        )
    finally:
        connection.close()


def _record_encodes(
    connection,
    movies,
    output_dir,
    config,
    dry_run,
    overwrite,
    quality,
    target_vmaf,
    remove_bars,
    workers,
    threads,
    db_file,
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
        remove_bars (bool): Crop stable baked-in black bars (quality mode).
        workers (int): Number of concurrent encodes (fixed mode).
        db_file (Path): The database file (failure-evidence logs location).
    """
    from shrinkray.encode import encode_movies
    from shrinkray.integrations import notify_after_encodes
    from shrinkray.quality import encode_with_quality

    # already encoded AND output still present? (unless --overwrite)
    # => batch resume: an interrupted run skips everything it finished before
    encoded_paths: set[str] = set()
    if not overwrite and not dry_run:
        encoded_paths = finished_movies(connection, movies, output_dir)

    encoded_for_notify: list[Path] = []
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
                    remove_bars=remove_bars,
                    db_file=db_file,
                    encoder_threads=cap_threads(threads or config.handbrake_max_threads),
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
                    record_quality_results(connection, movie, result, config, target_vmaf)
                    encoded_for_notify.append(movie)
                elif result.skipped_reason is not None:
                    # guardrail skip => record it so future runs do not re-probe
                    console.print(f"[yellow]Skipped '{movie.name}': {result.error}[/yellow]")
                    record_quality_results(connection, movie, result, config, target_vmaf)
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
            workers=workers if workers > 0 else None,
            max_threads=cap_threads(threads or config.handbrake_max_threads),
        )
        encoded_for_notify = [r.input for r in results if r.success]
    except (RuntimeError, FileNotFoundError) as e:
        log_error(str(e))
        raise typer.Exit(1) from e

    # record the encodes of this run in the database
    record_fixed_results(connection, results, config)

    # one polite tap on the media server's shoulder after the whole batch
    notify_after_encodes(config, encoded_for_notify)


@app.command()
def plan(
    path: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Root path of the movie folder, e.g. D:\\Movies",
    ),
    quality: bool = typer.Option(
        True,
        "--quality/--fixed",
        help="Plan quality mode (VMAF-driven) or the fixed preset.",
    ),
    target_vmaf: float = typer.Option(
        95.0,
        "--target-vmaf",
        min=1.0,
        max=100.0,
        help="VMAF quality floor for the quality plan.",
    ),
    remove_bars: bool = typer.Option(
        False, "--remove-bars", help="Include black-bar removal in the plan."
    ),
    threads: int = typer.Option(
        0,
        "--threads",
        min=0,
        help="Per-encode thread cap for the probes (0 = all cores).",
    ),
    db_file: Path = typer.Option(
        DB_FILE, "--db", help="Path of the sqlite database file (scan cache/history)."
    ),
):
    """Probes the movies and predicts the encode outcome without encoding.

    Shows which movies would be encoded (with predicted CRF, quality and
    estimated size) and which would be skipped (with the reason). Runs the
    full probing pipeline, so it takes roughly as long as the probing part
    of a real encode run.
    """
    from shrinkray.quality import plan_quality

    config = load_config()
    connection = db.connect(db_file)
    try:
        _, movies, _ = scan_movies(path, config, connection=connection)
    finally:
        connection.close()

    threads_cap = cap_threads(threads or config.handbrake_max_threads)

    table = Table(
        title=f"Encode plan for {len(movies)} movies (target VMAF {target_vmaf})",
        title_justify="left",
    )
    table.add_column("Movie", style="bold", max_width=32, overflow="ellipsis")
    table.add_column("Decision", justify="center", no_wrap=True)
    table.add_column("CRF", justify="right", no_wrap=True)
    table.add_column("VMAF", justify="right", no_wrap=True)
    table.add_column("Est. size", justify="right", no_wrap=True)
    table.add_column("Saved", justify="right", no_wrap=True)
    table.add_column("Note", max_width=40, overflow="fold")

    total_input = total_output = 0
    plans: list = []
    for movie in movies:
        try:
            result = plan_quality(
                movie,
                target_vmaf=target_vmaf,
                encoder=config.handbrake_encoder,
                remove_bars=remove_bars,
                db_file=db_file,
                encoder_threads=threads_cap,
            )
        except (RuntimeError, FileNotFoundError) as e:
            table.add_row(movie.name, "[red]error[/red]", "", "", "", "", str(e))
            continue
        plans.append(result)

        if result.skipped_reason is not None:
            table.add_row(
                movie.name,
                "[yellow]skip[/yellow]",
                str(result.chosen_crf or ""),
                str(result.achieved_vmaf or ""),
                "-",
                "-",
                result.skipped_reason,
            )
            total_input += result.input_size
            continue

        est_gb = result.output_size / 1024**3
        saved = result.saved_percent
        total_input += result.input_size
        total_output += result.output_size
        table.add_row(
            movie.name,
            "[green]encode[/green]",
            str(result.chosen_crf),
            f"{result.achieved_vmaf:.2f}" if result.achieved_vmaf else "",
            f"{est_gb:.2f} GB",
            f"{saved}%",
            "bars removed" if remove_bars else "",
        )

    console.print(table)
    if total_input > 0 and total_output > 0:
        console.print(
            f"[green]Plan: {len(plans)} movies, estimated total "
            f"{total_input / 1024**3:.2f} GB -> {total_output / 1024**3:.2f} GB "
            f"({round((1 - total_output / total_input) * 100, 1)}% saved)[/green]"
        )


@app.command()
def report(
    db_file: Path = typer.Option(DB_FILE, "--db", help="Path of the sqlite database file."),
    output: Path = typer.Option(
        Path("report.html"), "--output", "-o", help="Path of the HTML report."
    ),
):
    """Generates a self-contained HTML report from the database."""
    if not db_file.is_file():
        log_error(f"Database '{db_file}' not found! Run a scan first.")
        raise typer.Exit(1)

    generate_report(db_file, output)
    console.print(f"[green]Report written to '{output}'.[/green]")


@app.command("compare")
def compare(
    movie: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="The movie file whose encode history (generation chain) to show.",
    ),
    db_file: Path = typer.Option(DB_FILE, "--db", help="Path of the sqlite database file."),
):
    """Shows the provenance chain of a movie: every generation with its data.

    Each row documents one generation: codec, bitrate, size, CRF/VMAF (when
    quality mode) and the generation number. Re-encoding an encode chains a
    new row automatically — this is how generation drift becomes visible
    per title (e.g. rip 18.4 Mbit/s -> encode 14.9 Mbit/s).
    """
    if not db_file.is_file():
        log_error(f"Database '{db_file}' not found! Run a scan first.")
        raise typer.Exit(1)

    connection = db.connect(db_file)
    try:
        movie_str = str(movie.resolve())
        # the chain: rows whose output is this file, plus rows whose input
        # is this file (a re-encode of it) — ordered by generation
        rows = connection.execute(
            """
            SELECT e.*, f.name FROM encodes e
            JOIN files f ON f.id = e.file_id
            WHERE e.output_file = ? OR e.source_file = ? OR f.path = ?
            ORDER BY e.generation, e.id
            """,
            (movie_str, movie_str, movie_str),
        ).fetchall()
        real = [r for r in rows if r["output_size"] > 0]
        if not real:
            console.print(
                f"[yellow]No encodes recorded for '{movie.name}' yet — "
                "run an encode first.[/yellow]"
            )
            return
        table = Table(title=f"Provenance chain of {movie.name}", title_justify="left")
        table.add_column("Gen", justify="right")
        table.add_column("Codec")
        table.add_column("Bitrate", justify="right")
        table.add_column("Size", justify="right")
        table.add_column("CRF", justify="right")
        table.add_column("VMAF", justify="right")
        table.add_column("Encoded", justify="right")
        for r in real:
            bitrate = f"{r['input_bitrate']:.2f}" if r["input_bitrate"] is not None else "—"
            vmaf = f"{r['achieved_vmaf']:.2f}" if r["achieved_vmaf"] is not None else "—"
            table.add_row(
                str(r["generation"]),
                r["source_codec"] or "—",
                bitrate,
                f"{r['input_size'] / (1024**3):.2f} GB",
                str(r["chosen_crf"] or "—"),
                vmaf,
                str(r["encoded_at"] or "—"),
            )
        console.print(table)
    finally:
        connection.close()


@app.command()
def watch_mode(
    inbox: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        readable=True,
        help="Directory to watch for new movies.",
    ),
    output_dir: Path = typer.Argument(..., help="Directory for the encoded files."),
    target_vmaf: float = typer.Option(
        95.0,
        "--target-vmaf",
        min=1.0,
        max=100.0,
        help="VMAF quality floor.",
    ),
    remove_bars: bool = typer.Option(
        False, "--remove-bars", help="Detect and crop stable black bars."
    ),
    poll: int = typer.Option(60, "--poll", min=5, help="Seconds between inbox checks."),
):
    """Watches a folder and encodes newly arrived movies automatically.

    Sources stay untouched (outputs land in the output directory), so the
    library only changes when you swap the files yourself. Stop with Ctrl+C;
    the running encode finishes first.
    """
    from shrinkray.watch import watch

    try:
        watch(
            inbox,
            output_dir,
            target_vmaf=target_vmaf,
            remove_bars=remove_bars,
            encoder=load_config().handbrake_encoder,
            poll_interval=float(poll),
            max_threads=cap_threads(load_config().handbrake_max_threads),
        )
    except (RuntimeError, FileNotFoundError) as e:
        log_error(str(e))
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
