import os
import sys
import pathlib
from pathlib import Path
from src.media.read_media import *
from src.helper.helpers import *


def main():
    root_dir = None
    # move try catch to  if __name__ == ...
    try:
    # get path as argument
        root_dir = sys.argv[1]
    except Exception as e:
        debug("Root path missing!", DEBUG_TYPE.ERROR)
        raise

    dir = Path(root_dir)
    dict = {}

    # time and size are in global space => reduce access time to not iterate through arrays/dictionaries again
    t_time = None
    t_size = 0

    # search for files with .mkv files recursive
    for index, filename in enumerate(dir.glob('**/*.mkv')):
        name = filename.parent.name
        category = filename.parent.parent.name
        ret = get_data(filename)
        size = convert_unit(os.path.getsize(filename), SIZE_UNIT.GB)
        compression_rate = round(size / (ret['raw']['duration_raw'] / 3600), 2)

        # check for cropping
        ret['crop'] = check_black_bars(filename, ret['dimensions'])

        # remove raw data from dictionary
        del ret['raw']

        # add additional data to dictionary
        ret['name'] = name
        ret['category'] = category
        ret['size'] = size
        ret['compression_rate'] = compression_rate

        # set global values for time and size
        t_time = calc_time(t_time, ret['duration'])
        t_size += ret['size']

        if category in dict:
            dict[category].append(ret)
        else:
            dict[category] = [ret]

    # write data to file
    with open('movies.txt', 'w+') as text:
        # write csv / file header
        text.write('Kategorie;Name;Dauer;Größe;Codec;Aspect Ratio;Auflösung;Kompressionsrate;Bars\n')

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
        max_stuff = ";Gesamt;" + t_time + ";" + sanitize_number(t_size) + ";;;;;"
        text.write(max_stuff)

if __name__ == "__main__":
    # args for black border check and recoding => always to hvec
    # border = bool(sys.argv[2])
    # recode = bool(sys.argv[3])
    main()
    #sys.exit(main())