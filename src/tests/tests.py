import unittest

try:
    # run from the project root => 'python -m unittest discover -s src/tests'
    from src.helper import config_helper, helpers
    from src.media import read_media
except ImportError:
    # run from src/tests directly => 'python tests.py'
    import sys
    sys.path.append(sys.path[0] + '/..')
    from helper import config_helper, helpers
    from media import read_media


class TestHelpers(unittest.TestCase):

    def test_convert_unit(self):
        self.assertEqual(helpers.convert_unit(1024, helpers.SIZE_UNIT.KB), 1)
        self.assertEqual(helpers.convert_unit(1048576, helpers.SIZE_UNIT.MB), 1)
        self.assertEqual(helpers.convert_unit(1073741824, helpers.SIZE_UNIT.GB), 1)
        self.assertEqual(helpers.convert_unit(123, helpers.SIZE_UNIT.BYTES), 123)

    def test_sanitize_number(self):
        self.assertEqual(helpers.sanitize_number(0), '0')
        self.assertEqual(helpers.sanitize_number(1.5), '1,5')
        self.assertEqual(helpers.sanitize_number(10), '10')
        self.assertEqual(helpers.sanitize_number('3.14'), '3,14')

    def test_timestr_to_int(self):
        self.assertEqual(helpers.timestr_to_int('01:02:03'), 3723)
        self.assertEqual(helpers.timestr_to_int('00:00:00'), 0)
        self.assertEqual(helpers.timestr_to_int(None), 0)
        self.assertEqual(helpers.timestr_to_int('garbage'), 0)

    def test_calc_time(self):
        self.assertEqual(helpers.calc_time(None, '01:00:00'), '01:00:00')
        self.assertEqual(helpers.calc_time('01:00:00', None), '01:00:00')
        self.assertEqual(helpers.calc_time('00:59:59', '00:00:01'), '01:00:00')
        self.assertEqual(helpers.calc_time('10:00:00', '05:30:15'), '15:30:15')

    def test_get_other_codecs(self):
        self.assertEqual(helpers.get_other_codecs([]), '')
        self.assertEqual(helpers.get_other_codecs([{'name': 'aac'}]), 'aac')
        self.assertEqual(helpers.get_other_codecs(
            [{'name': 'aac'}, {'name': 'subrip'}]), 'aac, subrip')

    def test_generate_string(self):
        item = {'category': 'Movies', 'name': 'Test', 'duration': '01:00:00',
                'size': 1.5, 'codec': 'h264', 'bitrate': 2.5,
                'aspect_ratio': '16x9', 'dimensions': {'width': 1920, 'height': 1080},
                'compression_rate': 1.5, 'fps': 25, 'crop': 0, 'hdr': 0,
                'other_codecs': 'aac, subrip', 'audio_languages': 'de, en'}
        row = helpers.generate_string(item, ';', True)
        self.assertEqual(row, 'Movies;Test;01:00:00;1,5;h264;2,5;16x9;1920x1080;1,5;25;0;0;aac, subrip;de, en\n')
        # without category => empty first column
        row = helpers.generate_string(item, ';', False)
        self.assertTrue(row.startswith(';Test;'))


class TestReadMedia(unittest.TestCase):

    def test_parse_frame_rate(self):
        self.assertEqual(read_media.parse_frame_rate('25/1'), 25.0)
        self.assertAlmostEqual(read_media.parse_frame_rate(
            '30000/1001'), 29.97002997, places=6)
        self.assertEqual(read_media.parse_frame_rate('29.97'), 29.97)
        self.assertIsNone(read_media.parse_frame_rate('0/0'))
        self.assertIsNone(read_media.parse_frame_rate(None))
        self.assertIsNone(read_media.parse_frame_rate('garbage'))

    def test_get_raw_duration(self):
        self.assertEqual(read_media.get_raw_duration(
            {'tags': {'NUMBER_OF_FRAMES-eng': 100}, 'avg_frame_rate': '25/1'}), 4.0)
        self.assertEqual(read_media.get_raw_duration(
            {'nb_frames': 100, 'avg_frame_rate': '30000/1001'}), 100 / (30000/1001))
        self.assertIsNone(read_media.get_raw_duration({}))
        self.assertIsNone(read_media.get_raw_duration(
            {'tags': {'NUMBER_OF_FRAMES': 10}, 'avg_frame_rate': '0/0'}))

    def test_get_duration(self):
        self.assertEqual(read_media.get_duration(
            {'tags': {'DURATION-eng': '01:02:03.400000000'}}), '01:02:03')
        self.assertEqual(read_media.get_duration(
            {'tags': {'DURATION-de': '01:02:03.400000000'}}), '01:02:03')
        self.assertEqual(read_media.get_duration(
            {'tags': {'DURATION': '01:02:03.400000000'}}), '01:02:03')
        self.assertIsNone(read_media.get_duration({'tags': {}}))
        self.assertIsNone(read_media.get_duration({}))
        # configured language takes precedence
        self.assertEqual(read_media.get_duration(
            {'tags': {'DURATION-de': '02:00:00', 'DURATION-eng': '01:00:00'}}, 'de'), '02:00:00')
        self.assertEqual(read_media.get_duration(
            {'tags': {'DURATION-de': '02:00:00', 'DURATION-eng': '01:00:00'}}, 'en'), '01:00:00')

    def test_get_video_stream(self):
        streams = [{'codec_type': 'audio'}, {
            'codec_type': 'video', 'codec_name': 'h264'}]
        self.assertEqual(read_media.get_video_stream(streams)['codec_name'], 'h264')
        self.assertIsNone(read_media.get_video_stream([]))
        self.assertIsNone(read_media.get_video_stream(
            [{'codec_type': 'audio'}]))

    def test_get_audio_languages(self):
        streams = [{'codec_type': 'audio', 'tags': {'language': 'de'}},
                   {'codec_type': 'audio', 'tags': {'language': 'en'}},
                   {'codec_type': 'audio', 'tags': {}},
                   {'codec_type': 'video', 'tags': {'language': 'de'}}]
        self.assertEqual(read_media.get_audio_languages(streams), 'de, en')
        self.assertEqual(read_media.get_audio_languages([]), '')

    def test_get_aspect_ratio(self):
        self.assertEqual(read_media.get_aspect_ratio(1920, 1080), '16x9')
        self.assertEqual(read_media.get_aspect_ratio(3840, 2160), '16x9')

    def test_is_hdr(self):
        config = config_helper.Conf({'codec': {'hdr': {'color_space': ['bt2020nc'],
                                         'color_transfer': ['smpte2084']}}})
        hdr_stream = {'color_space': 'bt2020nc',
                      'color_transfer': 'smpte2084'}
        sdr_stream = {'color_space': 'bt709', 'color_transfer': 'bt709'}
        self.assertTrue(read_media.is_hdr(hdr_stream, config))
        self.assertFalse(read_media.is_hdr(sdr_stream, config))
        self.assertFalse(read_media.is_hdr({}, config))


if __name__ == "__main__":
    unittest.main()
