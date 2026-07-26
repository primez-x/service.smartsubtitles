import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).parents[1] / 'resources' / 'lib' / 'subtitles.py'


def load_subtitles_module():
    language_codes = {
        'en': 'eng',
        'eng': 'eng',
        'english': 'eng',
        'it': 'ita',
        'ita': 'ita',
        'italian': 'ita',
    }
    xbmc = types.SimpleNamespace(
        ISO_639_2=0,
        LOGINFO=1,
        LOGDEBUG=0,
        NOTIFICATION_INFO=0,
        convertLanguage=lambda language, _format: language_codes.get(language.lower(), ''),
        getLocalizedString=lambda _identifier: 'Subtitles',
        executeJSONRPC=lambda _payload: '{}',
        log=lambda _message, _level: None,
    )
    xbmcgui = types.SimpleNamespace(Dialog=lambda: types.SimpleNamespace(notification=lambda *args: None))
    with mock.patch.dict(sys.modules, {'xbmc': xbmc, 'xbmcgui': xbmcgui}):
        spec = importlib.util.spec_from_file_location('smart_subtitles_under_test', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class SmartSubtitlesTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()

    def test_normalizes_common_english_language_values(self):
        self.assertEqual('eng', self.subtitles._language_code('English'))
        self.assertEqual('eng', self.subtitles._language_code('en'))
        self.assertEqual('eng', self.subtitles._language_code('eng'))
        self.assertEqual('eng', self.subtitles._language_code('en_US'))
        self.assertEqual('ita', self.subtitles._language_code('Italian'))

    def test_selects_english_forced_track_when_italian_comes_first(self):
        streams = [
            {'index': 1, 'language': 'ita', 'name': 'Italiano Forced'},
            {'index': 2, 'language': 'eng', 'name': 'English Forced'},
        ]
        with mock.patch.object(self.subtitles, '_get_setting_bool', return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id', return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams', return_value=(streams, {}, False)), \
                mock.patch.object(self.subtitles, '_get_keywords', return_value=['forced']), \
                mock.patch.object(self.subtitles, '_get_preferred_languages', return_value={'eng'}), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_subtitle, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            result = self.subtitles.select_signs_songs_track()

        self.assertEqual('selected', result)
        set_subtitle.assert_called_once_with(1, 2)

    def test_never_selects_a_non_preferred_forced_track(self):
        streams = [{'index': 1, 'language': 'ita', 'name': 'Italiano Forced'}]
        with mock.patch.object(self.subtitles, '_get_setting_bool', return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id', return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams', return_value=(streams, {}, False)), \
                mock.patch.object(self.subtitles, '_get_keywords', return_value=['forced']), \
                mock.patch.object(self.subtitles, '_get_preferred_languages', return_value={'eng'}), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_subtitle:
            result = self.subtitles.select_signs_songs_track()

        self.assertEqual('no_match', result)
        set_subtitle.assert_not_called()

    def test_replaces_an_active_non_preferred_forced_track(self):
        streams = [
            {'index': 1, 'language': 'ita', 'name': 'Italiano Forced', 'isforced': True},
            {'index': 2, 'language': 'eng', 'name': 'English Forced', 'isforced': True},
        ]
        with mock.patch.object(self.subtitles, '_get_setting_bool', return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id', return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams', return_value=(
                    streams, {'index': 1}, True)), \
                mock.patch.object(self.subtitles, '_get_keywords', return_value=['forced']), \
                mock.patch.object(self.subtitles, '_get_preferred_languages', return_value={'eng'}), \
                mock.patch.object(self.subtitles, '_disable_subtitles') as disable_subtitles, \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_subtitle, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            result = self.subtitles.select_signs_songs_track()

        self.assertEqual('selected', result)
        disable_subtitles.assert_called_once_with(1)
        set_subtitle.assert_called_once_with(1, 2)

    def test_does_not_override_an_active_preferred_track_at_index_zero(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'English Forced', 'isforced': True},
            {'index': 1, 'language': 'ita', 'name': 'Italiano Forced', 'isforced': True},
        ]
        with mock.patch.object(self.subtitles, '_get_setting_bool', return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id', return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams', return_value=(
                    streams, {'index': 0}, True)), \
                mock.patch.object(self.subtitles, '_get_keywords', return_value=['forced']), \
                mock.patch.object(self.subtitles, '_get_preferred_languages', return_value={'eng'}), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_subtitle:
            result = self.subtitles.select_signs_songs_track()

        self.assertEqual('skip', result)
        set_subtitle.assert_not_called()
