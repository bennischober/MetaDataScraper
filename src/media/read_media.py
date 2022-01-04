import ffmpeg
import math

def get_aspect_ratio(width, height):
    gcd = math.gcd(width, height)
    lhs = int(width / gcd)
    rhs = int(height / gcd)
    return f"{lhs}:{rhs}"


def get_data(path) :
    # read the audio/video file from the command line arguments
    media_file = str(path)
    # uses ffprobe command to extract all possible metadata from the media file
    vid = ffmpeg.probe(media_file)["streams"]
    codec = vid[0]['codec_name']
    duration = vid[0]['tags']['DURATION-eng'].split('.')[0]
    # check framerate at index 0 and 1, because its given like '25/1'
    duration_raw =  int(vid[0]['tags']['NUMBER_OF_FRAMES-eng']) / int((vid[0]['avg_frame_rate'][0] + vid[0]['avg_frame_rate'][1]))
    height = vid[0]['height']
    width = vid[0]['width']
    aspect_ratio = get_aspect_ratio(width, height)
    return {"codec": codec, "duration": duration, "aspect_ratio": aspect_ratio, "dimensions": {"width": width, "height": height}, "raw": {"duration_raw": duration_raw}}

# old stuff
# import ffmpeg
# vid = ffmpeg.probe("title_t00.mkv")
# print(vid['streams'])
# tmp = vid['streams']
# codec = tmp[0]['codec_name']
# print(codec)