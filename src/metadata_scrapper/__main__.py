import argparse
import csv
import os
from pathlib import Path

from tqdm import tqdm

from metadata_scrapper.config import load_config
from metadata_scrapper.helpers import (
    SIZE_UNIT,
    calc_time,
    check_black_bars,
    convert_unit,
    generate_string,
    get_other_codecs,
    sanitize_number,
    timestr_to_int,
)
from metadata_scrapper.logger import log_error, log_warning
from metadata_scrapper.media import get_data

OUTPUT_FILE = "movies.txt"
CSV_FILE = "output.csv"


def parse_arguments() -> argparse.Namespace:
    """Parses the command line arguments.

    Returns:
        argparse.Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        prog="metadata-scrapper", description="A short python script to get video meta data."
    )
    parser.add_argument("path", type=str, help="Root path of the movie folder, e.g. D:\\Movies")
    parser.add_argument(
        "-csv",
        "--csv",
        dest="csv",
        default=False,
        action="store_true",
        help="Also write the data as csv file.",
    )
    parser.add_argument(
        "-e",
        "--encode",
        dest="encode",
        metavar="DIR",
        default=None,
        help="Re-encode the found movies with HandBrake into DIR (needs HandBrakeCLI).",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        default=False,
        action="store_true",
        help="Show what would be encoded without encoding.",
    )
    return parser.parse_args()


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


def write_output(movies_by_category: dict, total_time, total_size, separator: str):
    """Writes the processed data to the output file.

    Args:
        movies_by_category (dict): Categories as keys and movie lists as values.
        total_time (str | None): Total duration as 'HH:MM:SS' string.
        total_size (float): Total size in GB.
        separator (str): The separator between the columns.
    """
    with open(OUTPUT_FILE, "w+", encoding="UTF8") as text:
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


def write_csv(separator: str):
    """Converts the output file into a real csv file.

    Args:
        separator (str): The separator between the columns.
    """
    with open(OUTPUT_FILE, encoding="utf-8") as in_file:
        lines = [line.rstrip("\n").split(separator) for line in in_file if line.strip()]
        with open(CSV_FILE, "w+", newline="", encoding="iso-8859-1") as out_file:
            writer = csv.writer(out_file, delimiter=";", quotechar='"', quoting=csv.QUOTE_MINIMAL)
            writer.writerows(lines)


def main():
    # handle arguments
    args = parse_arguments()
    config = load_config()

    root = Path(args.path)
    if not root.is_dir():
        log_error(f"Root path '{root}' is not a directory!")
        raise SystemExit(1)

    # stores the data => category as key, movies as value
    movies_by_category: dict[str, list[dict]] = {}

    # total time and size => collected while iterating
    total_time = None
    total_size = 0

    # search for movie files with the configured extensions, recursive
    movies = find_movies(root, config.extensions)
    if not movies:
        log_warning(f"No movie files found in '{root}' with extensions {config.extensions}!")
        return

    error_messages = []

    for filename in tqdm(movies, dynamic_ncols=True):
        name = get_name(filename)
        category = filename.parent.parent.name
        try:
            ret = get_data(filename, config)
        except Exception as e:
            # move errors to stack/array and show at the end
            error_messages.append(
                f"\nA reading error occured in '{name}' at '{filename}'. Exception: {e}\n"
            )
            continue

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

        # set total values for time and size
        if ret["duration"] is not None:
            total_time = calc_time(total_time, ret["duration"])
        total_size += ret["size"]

        movies_by_category.setdefault(category, []).append(ret)

    # write data to file => move out of loop
    write_output(movies_by_category, total_time, total_size, config.separator)

    # create csv file
    if args.csv:
        write_csv(config.separator)

    # re-encode movies with handbrake
    if args.encode:
        from metadata_scrapper.encode import encode_movies

        try:
            encode_movies(
                movies,
                Path(args.encode),
                preset=config.handbrake_preset,
                encoder=config.handbrake_encoder,
                dry_run=args.dry_run,
            )
        except (RuntimeError, FileNotFoundError) as e:
            log_error(str(e))

    # show errors
    for er in error_messages:
        log_error(er)


if __name__ == "__main__":
    main()
