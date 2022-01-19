import enum
import subprocess

# Enum for size units
class SIZE_UNIT(enum.Enum):
    BYTES = 1
    KB = 2
    MB = 3
    GB = 4


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

def generate_string(item, useCat=False):
    # function that generates a string with the given dictionary data

    cat = str(item['category']) + SEPERATOR
    name = str(item['name']) + SEPERATOR
    duration = str(item['duration']) + SEPERATOR
    size = sanitize_number(item['size']) + SEPERATOR
    codec = str(item['codec']) + SEPERATOR
    bitrate = sanitize_number(item['bitrate']) + SEPERATOR
    aspect_ratio = item['aspect_ratio'] + SEPERATOR
    dimensions = str(item['dimensions']['width']) + 'x' + \
        str(item['dimensions']['height']) + SEPERATOR
    comp_rate = sanitize_number(item['compression_rate']) + SEPERATOR
    crop = str(item['crop']) + SEPERATOR
    other_codecs = str(item.get('other_codecs', ''))

    if(useCat):
        return cat + name + duration + size + codec + bitrate + aspect_ratio + dimensions + comp_rate + crop + other_codecs + "\n"
    else:
        return SEPERATOR + name + duration + size + codec + bitrate + aspect_ratio + dimensions + comp_rate + crop + other_codecs + "\n"


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


def get_other_codecs(other_codecs):
    l = len(other_codecs)
    it = 0
    out = ""
    for codec in other_codecs:
        if it == l - 1:
            # last item
            out += codec['name']
        else:
            out += (codec['name'] + ", ")
        it += 1
    return out

MIN_WIDTH = 100
MIN_HEIGHT = 100


def check_black_bars(file, dimensions):
    if dimensions['width'] == None or dimensions['height'] == None:
        return 0

    pres = subprocess.Popen('ffmpeg -ss 90 -i ' '"' + str(file) +
                            '"' ' -vframes 10 -vf cropdetect -f null -', stdout=subprocess.PIPE, stderr=subprocess.PIPE)
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
