import subprocess
from enum import Enum

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)


class SIZE_UNIT(Enum):
    BYTES = 1
    KB = 2
    MB = 3
    GB = 4


def convert_unit(size_in_bytes, unit):
    """Convert the size from bytes to other units like KB, MB or GB"""
    if unit == SIZE_UNIT.KB:
        return round(size_in_bytes / 1024, 2)
    elif unit == SIZE_UNIT.MB:
        return round(size_in_bytes / (1024 * 1024), 2)
    elif unit == SIZE_UNIT.GB:
        return round(size_in_bytes / (1024 * 1024 * 1024), 2)
    else:
        return size_in_bytes


# function to sanitize decimal numbers for excel
def sanitize_number(number) -> str:
    """Converts a number to a string with ',' as decimal separator (excel friendly).

    Integers are converted without decimal places (e.g. 0 => '0').

    Args:
        number: The number to sanitize.

    Returns:
        str: The sanitized number as string.
    """
    number = str(number)
    parts = number.split(".")
    if len(parts) == 1:
        return parts[0]
    return parts[0] + "," + parts[1]


def generate_string(item, separator=";", useCat=False) -> str:
    """Function that generates an output string (row) with the given dictionary data.

    Args:
        item (dict): The movie data.
        separator (str): The separator between the columns.
        useCat (bool): Whether the category should be written or not.

    Returns:
        str: The output string.
    """
    cat = str(item["category"])
    name = str(item["name"])
    duration = str(item["duration"])
    size = sanitize_number(item["size"])
    codec = str(item["codec"])
    bitrate = sanitize_number(item["bitrate"])
    aspect_ratio = str(item["aspect_ratio"])
    dimensions = str(item["dimensions"]["width"]) + "x" + str(item["dimensions"]["height"])
    comp_rate = sanitize_number(item["compression_rate"])
    fps = str(item["fps"])
    crop = str(item["crop"])
    hdr = str(item.get("hdr", ""))
    other_codecs = str(item.get("other_codecs", ""))
    audio_languages = str(item.get("audio_languages", ""))

    row = [
        cat if useCat else "",
        name,
        duration,
        size,
        codec,
        bitrate,
        aspect_ratio,
        dimensions,
        comp_rate,
        fps,
        crop,
        hdr,
        other_codecs,
        audio_languages,
    ]
    return separator.join(row) + "\n"


def timestr_to_int(time) -> int:
    """Converts a 'HH:MM:SS' string into seconds.

    Args:
        time (str): The time as 'HH:MM:SS' string.

    Returns:
        int: The time in seconds, 0 if the string is invalid.
    """
    if not time:
        return 0
    parts = time.split(":")
    if len(parts) < 2:
        return 0

    try:
        hours = int(parts[0]) * 3600
        minutes = int(parts[1]) * 60
        seconds = int(float(parts[2]))
        return hours + minutes + seconds
    except ValueError:
        return 0


def calc_time(lhs, rhs) -> str | None:
    """Adds two 'HH:MM:SS' time strings and returns the sum.

    Args:
        lhs (str | None): The first time as 'HH:MM:SS' string (or None).
        rhs (str | None): The second time as 'HH:MM:SS' string (or None).

    Returns:
        str | None: The sum as 'HH:MM:SS' string.
    """
    if lhs is None:
        return rhs
    if rhs is None:
        return lhs

    lhs_parts = lhs.split(":")
    if len(lhs_parts) < 2:
        return rhs

    rhs_parts = rhs.split(":")
    if len(rhs_parts) < 2:
        return lhs

    try:
        # add seconds first
        seconds = int(float(lhs_parts[2])) + int(float(rhs_parts[2]))
        # check for seconds > 60
        minutes = int(seconds / 60)
        # add rest to seconds
        seconds = seconds % 60

        # add existing minutes
        minutes += int(lhs_parts[1]) + int(rhs_parts[1])
        hours = int(minutes / 60)
        minutes = minutes % 60

        # add existing hours
        hours += int(lhs_parts[0]) + int(rhs_parts[0])
    except ValueError:
        return lhs

    # checks for putting a 0 before the time value
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def get_other_codecs(other_codecs) -> str:
    """Converts the other codecs list into a comma separated string of codec names.

    Args:
        other_codecs (list): List of codec dictionaries.

    Returns:
        str: Comma separated codec names, e.g. 'aac, subrip'.
    """
    return ", ".join(codec["name"] for codec in other_codecs)


def check_black_bars(file, dimensions, min_width=100, min_height=100) -> int:
    """Checks if a movie has (relevant) black bars by using the ffmpeg cropdetect filter.

    Some frames in the middle of the movie are checked against the codec data
    (using a threshold to be sure its not just some pixels that can be cut).

    Args:
        file (Path): Path to the movie file.
        dimensions (dict): Dictionary with the 'width' and 'height' of the video.
        min_width (int): Minimum width difference to detect black bars.
        min_height (int): Minimum height difference to detect black bars.

    Returns:
        int: 1 if black bars were detected, 0 if not (or it could not be checked).
    """
    if dimensions["width"] is None or dimensions["height"] is None:
        return 0

    try:
        pres = subprocess.run(
            [
                "ffmpeg",
                "-ss",
                "90",
                "-i",
                str(file),
                "-vframes",
                "10",
                "-vf",
                "cropdetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            timeout=60,
        )
        cdetect = str(pres.stderr)
    except (OSError, subprocess.SubprocessError):
        return 0

    # cropdetect may not find any crop => no black bars
    if "crop=" not in cdetect:
        return 0

    cres = cdetect.split("crop=")
    dres = cres[1].split(":")
    try:
        w = int(dres[0].split("'")[0])
        h = int(dres[1].split("'")[0])
    except (IndexError, ValueError):
        return 0

    if w + min_width < dimensions["width"]:
        return 1
    if h + min_height < dimensions["height"]:
        return 1
    return 0


def create_movie_progress() -> Progress:
    """Creates the rich progress bar used while scanning movies.

    Returns:
        Progress: The progress bar.
    """
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
    )
