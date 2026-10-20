import argparse
import csv
import gc
import os
from pathlib import Path

from tqdm import tqdm

from src.helper.config_helper import load_config
from src.helper.helpers import SIZE_UNIT, calc_time, check_black_bars, convert_unit, generate_string, get_other_codecs, sanitize_number, timestr_to_int
from src.helper.logger import log_error, log_warning
from src.media.read_media import get_data


def parse_arguments():
    """Parses the command line arguments.

    Returns:
        Namespace: The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description='A short python script to get video meta data.')
    parser.add_argument('path', type=str,
                        help='Root path of the movie folder, e.g. D:\\Movies')
    parser.add_argument('-csv', default=False, action='store_true',
                        help='Also write the data as csv file.')
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


def find_movies(dir: Path, extensions: list[str]) -> list[Path]:
    """Finds all movie files with the given extensions recursively.

    Args:
        dir (Path): Root directory to search in.
        extensions (list[str]): Extensions to search for, e.g. ['.mkv', '.mp4'].

    Returns:
        list[Path]: List of movie files.
    """
    movies: list[Path] = []
    for extension in extensions:
        extension = extension.lower().lstrip('.')
        # glob is case sensitive on linux => check both cases
        movies.extend(dir.glob(f'**/*.{extension}'))
        movies.extend(dir.glob(f'**/*.{extension.upper()}'))
    # on windows glob is case insensitive => the same file is found twice
    return list(dict.fromkeys(movies))


def write_output(dict: dict, t_time, t_size, separator: str):
    """Writes the processed data to the movies.txt output file.

    Args:
        dict (dict): Dictionary with the categories as keys and movie lists as values.
        t_time (str | None): Total duration as 'HH:MM:SS' string.
        t_size (float): Total size in GB.
        separator (str): The separator between the columns.
    """
    with open('movies.txt', 'w+', encoding="UTF8") as text:
        # write csv / file header
        header = ['CATEGORY', 'NAME', 'DURATION', 'SIZE', 'CODEC', 'BITRATE', 'ASPECT RATIO',
                  'RESOLUTION', 'GB/h', 'FPS', 'BARS', 'HDR', 'OTHER CODECS', 'AUDIO LANGUAGES']
        text.write(separator.join(header) + '\n')

        # iterate through parent dictionary
        for key in dict:
            # variables for category check
            cat = ''
            use = False

            # write value for category / index in dictionary
            dic_wrt = ''

            # iterate through array at index
            for value in dict[key]:
                # check if category should be shown or not
                if cat == '' or cat != value['category']:
                    use = True
                    cat = value['category']
                else:
                    use = False
                dic_wrt += generate_string(value, separator, use)

            text.write(dic_wrt)

        # write total duration and size
        total = ['Total', str(t_time), sanitize_number(t_size)]
        total.extend([''] * (len(header) - len(total)))
        text.write(separator.join(total) + '\n')


def write_csv(separator: str):
    """Converts the movies.txt output file into a real csv file.

    Args:
        separator (str): The separator between the columns.
    """
    with open('movies.txt', "r", encoding="utf-8") as in_file:
        lines = [line.rstrip('\n').split(separator)
                 for line in in_file if line.strip()]
        with open('output.csv', 'w+', newline='', encoding="iso-8859-1") as out_file:
            writer = csv.writer(out_file, delimiter=';',
                                quotechar='"', quoting=csv.QUOTE_MINIMAL)
            writer.writerows(lines)


def main():
    # handle arguments
    args = parse_arguments()
    config = load_config()

    dir = Path(args.path)
    if not dir.is_dir():
        log_error(f"Root path '{dir}' is not a directory!")
        raise SystemExit(1)

    # stores the data => category as key, movies as value
    dict = {}

    # time and size are in 'global' space => reduce access time to not iterate through arrays/dictionaries again
    t_time = None
    t_size = 0

    # search for movie files with the configured extensions, recursive
    movies = find_movies(dir, config.extensions)
    if not movies:
        log_warning(
            f"No movie files found in '{dir}' with extensions {config.extensions}!")
        return

    error_messages = []

    for filename in tqdm(movies, dynamic_ncols=True):
        name = get_name(filename)

        category = filename.parent.parent.name
        try:
            ret = get_data(filename, config)  # try catch with error => movie name and path
        except Exception as e:
            # move errors to stack/array and show at the end => "Some errors occured during the execution. Check 'errors.log' for more information."
            error_messages.append(
                f"\nA reading error occured in '{name}' at '{filename}'. Exception: {e}\n")
            continue

        size = convert_unit(os.path.getsize(filename), SIZE_UNIT.GB)

        # it's not really the compression rate, it's rather size/time
        compression_rate = 0
        if ret['duration_raw'] is not None:
            compression_rate = round(size / (ret['duration_raw'] / 3600), 2)
        elif ret['duration'] is not None:
            compression_rate = round(
                size / (timestr_to_int(ret['duration']) / 3600), 2)

        # check for cropping
        ret['crop'] = check_black_bars(
            filename, ret['dimensions'],
            min_width=config.black_bar_min_width,
            min_height=config.black_bar_min_height)

        # remove raw data from dictionary
        ret.pop('duration_raw', None)
        gc.collect()

        # add additional data to dictionary
        ret['name'] = name
        ret['category'] = category
        ret['size'] = size
        ret['compression_rate'] = compression_rate  # is SIZE/TIME
        ret['other_codecs'] = get_other_codecs(ret['other_codecs'])

        # set global values for time and size
        if ret['duration'] is not None:
            t_time = calc_time(t_time, ret['duration'])
        t_size += ret['size']

        if category in dict:
            dict[category].append(ret)
        else:
            dict[category] = [ret]

    # write data to file => move out of loop
    write_output(dict, t_time, t_size, config.separator)

    # create csv file
    if args.csv:
        write_csv(config.separator)

    if not error_messages:
        return

    # show errors
    for er in error_messages:
        log_error(er)


if __name__ == "__main__":
    main()
