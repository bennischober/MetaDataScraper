import ffmpeg
import math
from pprint import pprint

def get_aspect_ratio(width, height):
    gcd = math.gcd(width, height)
    lhs = int(width / gcd)
    rhs = int(height / gcd)
    return f"{lhs}:{rhs}"


def get_data(path) :
    # read the audio/video file from the command line arguments
    media_file = str(path)
    # uses ffprobe command to extract all possible metadata from the media file
    streams = ffmpeg.probe(media_file)["streams"]
    video = streams[0]
    codec = video['codec_name']

    # for other codecs => needs to be included in the output file!
    other_codecs = []
    for cd in streams:
        # creates object with name, type, language, title
        other_codecs.append({"name": cd.get('codec_name'), "type": cd.get('codec_type'), "language": cd['tags'].get('language', ''), "title": cd['tags'].get("title", '')})

    duration = None
    if 'DURATION-eng' in video['tags']:
        duration = video['tags']['DURATION-eng'].split('.')[0] # could also be DURATION => search for something with DURATION in its name; might be this one: [value for key, value in programs.items() if 'new york' in key.lower()]
    elif 'DURATION-de' in video['tags']:
        duration = video['tags']['DURATION-de'].split('.')[0]
    elif 'DURATION' in video['tags']:
        duration = video['tags']['DURATION'].split('.')[0]
    else:
        raise TypeError('Cant find duration in tags!')
    
    # check framerate at index 0 and 1, because its given like '25/1'
    duration_raw = None
    if 'NUMBER_OF_FRAMES-eng' in video['tags'] and 'avg_frame_rate' in video:
        duration_raw =  int(video['tags']['NUMBER_OF_FRAMES-eng']) / int((video['avg_frame_rate'][0] + video['avg_frame_rate'][1]))

    height = video['height']
    width = video['width']
    aspect_ratio = get_aspect_ratio(width, height)

    # clear data
    del streams, video

    return {"codec": codec, "other_codecs": other_codecs, "duration": duration, "aspect_ratio": aspect_ratio, "dimensions": {"width": width, "height": height}, "raw": {"duration_raw": duration_raw}}
