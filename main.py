import os
import gc
import io
import argparse
import csv
from tqdm import tqdm
from pathlib import Path
from src.media.read_media import get_data
from src.helper.helpers import *
from src.helper.logger import log_error


def main():
    # handle arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=str)
    parser.add_argument('-csv', default=False, action="store_true")
    args = parser.parse_args()

    root_dir = None
    try:
    # get path as argument
        root_dir = args.path
    except Exception as e:
        log_error("Root path missing!")
        raise

    dir = Path(root_dir)
    # stores the data => category as key, movies as value
    dict = {}

    # time and size are in 'global' space => reduce access time to not iterate through arrays/dictionaries again
    t_time = None
    t_size = 0

    # fix: need a way more efficient way to do this!
    movie_counter = 0
    for index, filename in enumerate(dir.glob('**/*.mkv')):
        movie_counter += 1

    error_messages = []

    # search for files with .mkv files recursive
    for index, filename in tqdm(enumerate(dir.glob('**/*.mkv')), total=movie_counter, dynamic_ncols=True):
        # check if there are multiple movies in one folder; if so => take movie names as name and not parent folder!
        name = filename.parent.name
        category = filename.parent.parent.name
        try:
            ret = get_data(filename) # try catch with error => movie name and path
        except TypeError as e:
            # move errors to stack/array and show at the end => "Some errors occured during the execution. Check 'errors.log' for more information."
            error_messages.append(str("\nA reading error occured in " + "'" + name + "'" + " at " + "'" + str(filename) + "'" + ". Exception: " + str(e) + "\n"))
            continue

        size = convert_unit(os.path.getsize(filename), SIZE_UNIT.GB)

        # it's not really the compression rate, it's rather size/time
        compression_rate = 0
        if ret['raw']['duration_raw'] != None:
            compression_rate = round(size / (ret['raw']['duration_raw'] / 3600), 2)
        elif ret['duration'] != None:
            compression_rate = round(size / (timestr_to_int(ret['duration']) / 3600), 2)

        # check for cropping
        ret['crop'] = check_black_bars(filename, ret['dimensions'])

        # remove raw data from dictionary
        del ret['raw']
        gc.collect()

        # add additional data to dictionary
        ret['name'] = name
        ret['category'] = category
        ret['size'] = size
        ret['compression_rate'] = compression_rate # is SIZE/TIME
        ret['other_codecs'] = get_other_codecs(ret['other_codecs'])

        # set global values for time and size
        if ret['duration'] != None:
            t_time = calc_time(t_time, ret['duration'])
        t_size += ret['size']

        if category in dict:
            dict[category].append(ret)
        else:
            dict[category] = [ret]

    # write data to file => move out of loop
    with open('movies.txt', 'w+', encoding="UTF8") as text:
        # write csv / file header
        text.write('CATEGORY;NAME;DURATION;SIZE (GB);CODEC;BITRATE (mbit/s);ASPECT RATIO;RESOLUTION;GB/h;Bars;OTHER CODECS\n')

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
                if(cat == '' or cat != value['category']):
                    use = True
                    cat = value['category']
                else:
                    use = False
                dic_wrt += generate_string(value, use)

            text.write(dic_wrt)
        max_stuff = ";Total;" + t_time + ";" + sanitize_number(t_size) + ";;;;;;;"
        text.write(max_stuff)

    # create csv file
    if args.csv:
        with open('movies.txt', "r", encoding="utf-8") as in_file:
            stripped = (line.strip() for line in in_file)
            lines = (line.split("\n") for line in stripped if line)
            with io.open('output.csv', 'w+', newline='', encoding="iso-8859-1") as out_file:
                writer = csv.writer(out_file, delimiter=';', quotechar=' ', quoting=csv.QUOTE_MINIMAL, dialect="excel")
                writer.writerows(lines)

    if not error_messages:
        return
    
    # show errors
    for er in error_messages:
        log_error(er)

if __name__ == "__main__":
    main()
