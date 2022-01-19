from decimal import Decimal
import ffmpeg
import math
import gc


def get_aspect_ratio(width, height):
    gcd = math.gcd(width, height)
    lhs = int(width / gcd)
    rhs = int(height / gcd)
    return f"{lhs}x{rhs}"


def get_raw_duration(video):
    duration_raw = None
    # check framerate at index 0 and 1, because its given like '25/1'
    # ToDo: add other sources for NUMBER_OF_FRAMES => check some files
    try:
        if 'NUMBER_OF_FRAMES-eng' in video['tags'] and 'avg_frame_rate' in video:
            duration_raw = int(video['tags']['NUMBER_OF_FRAMES-eng']) / \
                int((video['avg_frame_rate'][0] + video['avg_frame_rate'][1]))
    except:
        #raise TypeError('Some error happened during the calculation of the raw duration!')
        return duration_raw
    return duration_raw


def get_duration(video):
    duration = None
    try:
        if 'DURATION-eng' in video['tags']:
            # could also be DURATION => search for something with DURATION in its name; might be this one: [value for key, value in programs.items() if 'new york' in key.lower()]
            duration = video['tags']['DURATION-eng'].split('.')[0]
        elif 'DURATION-de' in video['tags']:
            duration = video['tags']['DURATION-de'].split('.')[0]
        elif 'DURATION' in video['tags']:
            duration = video['tags']['DURATION'].split('.')[0]
        else:
            raise TypeError('Cant find duration in tags!')
    except:
        #raise TypeError('Some error happened during the calculation of the duration!')
        return duration
    return duration


def try_get_width(video):
    width = None
    if 'width' in video:
        width = video['width']
    elif 'coded_width' in video:
        width = video['coded_width']
    return width


def try_get_height(video):
    height = None
    if 'height' in video:
        height = video['height']
    elif 'coded_height' in video:
        height = video['coded_height']
    return height


def get_data(path):
    # read the audio/video file from the command line arguments
    media_file = str(path)
    # uses ffprobe command to extract all possible metadata from the media file
    probe = ffmpeg.probe(media_file)
    bitrate = 0.00
    if 'format' in probe:
        bitrate = round(
            Decimal(probe['format'].get('bit_rate'))/(1024*1024), 2)
    streams = probe["streams"]
    video = streams[0]
    codec = video['codec_name']

    # for other codecs => needs to be included in the output file!
    other_codecs = []
    first_cd = True
    for cd in streams:
        if first_cd:
            first_cd = False
            continue
        # creates object with name, type, language, title
        codec_name = cd.get('codec_name', '')
        codec_type = cd.get('codec_type', '')
        codec_language = str
        codec_title = str
        if 'tags' in cd:
            codec_language = cd['tags'].get('language', '')
            codec_title = cd['tags'].get("title", '')
        other_codecs.append({"name": str(codec_name), "type": codec_type,
                            "language": codec_language, "title": codec_title})

    # ToDo: add FPS, and think of a good output for other codecs (e.g. ac3, eac3, aac) => so just comma seperated names
    # could also add audio language (comma seperated) and subtitle language

    duration = get_duration(video)
    duration_raw = get_raw_duration(video)

    height = try_get_height(video)
    width = try_get_width(video)

    aspect_ratio = '0x0' # might look for a better option => 16:9 - excel will convert this to datetime
    if width != None and height != None:
        aspect_ratio = get_aspect_ratio(width, height)

    # clear data
    del streams, video
    gc.collect()

    return {"codec": codec, "other_codecs": other_codecs, "bitrate": bitrate, "duration": duration, "aspect_ratio": aspect_ratio, "dimensions": {"width": width, "height": height}, "raw": {"duration_raw": duration_raw}}
