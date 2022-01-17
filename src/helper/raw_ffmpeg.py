import ffmpeg
import sys
import json

# helper to check movie info
def main():
    dir = sys.argv[1]
    vid = ffmpeg.probe(dir)["streams"]
    vid_raw = ffmpeg.probe(dir)

    with open('movie_info.json', 'w+') as text:
        text.write(json.dumps(vid))

    with open('movie_info_raw.json', 'w+') as raw_text:
        raw_text.write(json.dumps(vid_raw))

if __name__ == "__main__":
    main()
