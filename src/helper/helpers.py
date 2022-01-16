import enum
import subprocess
import sys

class DEBUG_TYPE(enum.Enum):
    WARNING = 0
    ERROR = 1
    OK = 2
    NORMAL = 3
    BOLD = 4
    UNDERLINE = 5
    END = 6


class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

# Enum for size units


class SIZE_UNIT(enum.Enum):
    BYTES = 1
    KB = 2
    MB = 3
    GB = 4


def debug(message, type):
    col = None
    match type:
        case DEBUG_TYPE.WARNING:
            col = bcolors.WARNING
        case DEBUG_TYPE.ERROR:
            col = bcolors.FAIL
        case DEBUG_TYPE.OK:
            col = bcolors.OKGREEN
        case DEBUG_TYPE.NORMAL:
            col = bcolors.HEADER
        case DEBUG_TYPE.BOLD:
            col = bcolors.BOLD
        case DEBUG_TYPE.UNDERLINE:
            col = bcolors.UNDERLINE
        case DEBUG_TYPE.END:
            col = bcolors.ENDC
    print(col + message + bcolors.ENDC)


def convert_unit(size_in_bytes, unit):
    """ Convert the size from bytes to other units like KB, MB or GB"""
    if unit == SIZE_UNIT.KB:
        return round(size_in_bytes/1024, 2)
    elif unit == SIZE_UNIT.MB:
        return round(size_in_bytes/(1024*1024), 2)
    elif unit == SIZE_UNIT.GB:
        return round(size_in_bytes/(1024*1024*1024), 2)
    else:
        return size_in_bytes

# function to sanitize decimal numbers for excel


def sanitize_number(number):
    tmp = str(number)
    parts = tmp.split('.')
    ret = ''
    if(len(parts) > 1):
        ret += parts[0] + "," + parts[1]
    return ret


SEPERATOR = ';'
# function that generates a string with the given dictionary data


def generate_string(item, useCat=False):
    # print(item)
    # ret = ''
    # for value in item:
    #    ret += str(item[value]) + SEPERATOR
    if(useCat):
        return str(item['category']) + SEPERATOR + str(item['name']) + SEPERATOR + str(item['duration']) + SEPERATOR + sanitize_number(item['size']) + SEPERATOR + str(item['codec']) + SEPERATOR + str(item['aspect_ratio']) + SEPERATOR + str(item['dimensions']['width']) + 'x' + str(item['dimensions']['height']) + SEPERATOR + sanitize_number(item['compression_rate']) + SEPERATOR + str(item['crop']) + "\n"
    else:
        return SEPERATOR + str(item['name']) + SEPERATOR + str(item['duration']) + SEPERATOR + sanitize_number(item['size']) + SEPERATOR + str(item['codec']) + SEPERATOR + str(item['aspect_ratio']) + SEPERATOR + str(item['dimensions']['width']) + 'x' + str(item['dimensions']['height']) + SEPERATOR + sanitize_number(item['compression_rate']) + SEPERATOR + str(item['crop']) + "\n"


def timestr_to_int(time):
    # cut at :
    parts = time.split(':')
    if(len(parts) < 2):
        return 0

    hours = int(parts[0]) * 3600
    minutes = int(parts[1]) * 60
    seconds = int(parts[2])
    return hours + minutes + seconds


def calc_time(lhs, rhs):
    if(lhs == None):
        return rhs

    lhs_parts = lhs.split(':')
    if(len(lhs_parts) < 2):
        return rhs

    rhs_parts = rhs.split(':')
    if(len(rhs_parts) < 2):
        return lhs

    # add seconds first
    seconds = int(lhs_parts[2]) + int(rhs_parts[2])
    # check for seconds > 60
    minutes = int(seconds / 60)
    # add rest to seconds
    seconds = seconds % 60

    # add existing minutes
    minutes += int(lhs_parts[1]) + int(rhs_parts[1])
    hours = int(minutes/60)
    minutes = minutes % 60

    # add existing hours
    hours += int(lhs_parts[0]) + int(rhs_parts[0])

    # checks for putting a 0 before the time value
    sec = str(seconds)
    if(seconds < 10):
        sec = '0' + str(seconds)

    min = str(minutes)
    if(minutes < 10):
        min = '0' + str(minutes)

    hr = str(hours)
    if(hours < 10):
        hr = '0' + str(hours)

    return hr + ":" + min + ":" + sec


def get_len(dict):
    length = None
    for key in dict:
        for value in dict[key]:
            length = calc_time(length, value['duration'])
    return length

MIN_WIDTH = 100
MIN_HEIGHT = 100
def check_black_bars(file, dimensions):
    pres = subprocess.Popen('ffmpeg -ss 90 -i ' '"' + str(file) + '"' ' -vframes 10 -vf cropdetect -f null -', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    cdetect = str(pres.stderr.read())
    cres = cdetect.split("crop=")
    dres = cres[1].split(":")
    w = dres[0]
    h = dres[1]
    if int(w) + MIN_WIDTH < dimensions['width']:
        return 1
    if int(h) + MIN_HEIGHT < dimensions['height']:
        return 1
    return 0

# by: https://gist.github.com/vladignatyev/06860ec2040cb497f0f3
def progress(count, total, status='', bar_len=60):
    filled_len = int(round(bar_len * count / float(total)))

    percents = round(100.0 * count / float(total), 1)
    bar = '=' * filled_len + '-' * (bar_len - filled_len)

    fmt = '%s [%s] %s%s' % (status, bar, percents, '%')
    print('\b' * len(fmt), end='')  # clears the line
    sys.stdout.write(fmt)
    sys.stdout.flush()
