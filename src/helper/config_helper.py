# this script loads all the config data and ships it to the other scripts => provides default values
import json
from typing import Any

Config: None | dict[str, Any] = None


def read_config(root: str):
    """Reads the config.json file and stores it in the global variable Config.

    Args:
        root (str): Root path of the project.
    """
    # get root path of project
    with open(str(root) + '\\config.json', 'r', encoding="utf-8") as config:
        test = json.load(config)

    global Config
    Config = test


def check_config(root: str):
    """Checks if the config is already loaded in the global space. If not, it will be loaded.

    Args:
        root (str): Root path of the project.
    """
    global Config
    if Config == None:
        read_config(root)


def get_blackbar_settings(root: str):
    """Returns the black bar settings from the config file.

    Args:
        root (str): Root path of the project.

    Returns:
        dict: Dictionary with the black bar settings.
    """
    # first of all check if config is in global space
    check_config(root)

    global Config

    if Config != None:
        # save access of values from config => instead of Config['ffmpeg']['black_bars']['MIN_WIDTH']
        width = Config.get('ffmpeg', {}).get(
            'black_bars', {}).get('MIN_WIDTH', None)
        height = Config.get('ffmpeg', {}).get(
            'black_bars', {}).get('MIN_HEIGHT', None)

        # cast types of width and height
        width = int(width) if width != None else None
        height = int(height) if height != None else None

        # returns a dict
        return {'width': width, 'height': height}


def get_codec(root: str):
    """Returns the codec from the config file.

    Args:
        root (str): Root path of the project.

    Returns:
        str: Codec from the config file.
    """
    check_config(root)

    global Config

    return None if Config == None else Config.get('codec', None)

    # if Config != None:
    #     codec: dict[str, Any] = Config.get('codec', {})
    #     print(type(codec['hdr']))

    #     # returns a dict
    #     return codec


class Conf:
    codec = None
    extensions = None
    ffmpeg = None
    language = None

    def __init__(self, root: str):
        with open(str(root) + '\\config.json', 'r', encoding="utf-8") as config:
            con: dict[str, Any] = json.load(config)
            self.codec = con.get('codec', None)
            self.extensions = con.get('file', {}).get('extensions', None)
            self.ffmpeg = {'black_bars': {
                'MIN_WIDTH': con.get('ffmpeg', {}).get('black_bars', {}).get('MIN_WIDTH', None),
                'MIN_HEIGHT': con.get('ffmpeg', {}).get('black_bars', {}).get('MIN_HEIGHT', None)
            }}
            self.language = con.get('language', "en")
    
    def print(self):
        print(self.codec)
        print(self.extensions)
        print(self.ffmpeg)
        print(self.language)

    def get_codec(self):
        return self.codec
    
    def get_extensions(self):
        return self.extensions
    
    def get_ffmpeg(self):
        return self.ffmpeg

    def get_language(self):
        return self.language


def test_config(root: str):
    msg = "Does not exist"
    status = "ERROR"
    check_config(root)
    if Config != None:
        msg = "Does exist"
        status = "OK"

    Conf(root).print()

    return {'status': status, "message": msg}


print(test_config('D:\\Projects\\Privat\\Other\\Python\\MetaDataScrapper'))
