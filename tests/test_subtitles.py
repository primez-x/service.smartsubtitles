import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).parents[1] / 'resources' / 'lib' / 'subtitles.py'


def load_subtitles_module():
    """Load subtitles.py with stubbed xbmc/xbmcgui modules."""
    language_codes = {
        'en': 'eng', 'eng': 'eng', 'english': 'eng',
        'it': 'ita', 'ita': 'ita', 'italian': 'ita',
        'pt': 'por', 'por': 'por', 'portuguese': 'por',
        'es': 'spa', 'spa': 'spa', 'spanish': 'spa',
    }
    xbmc = types.SimpleNamespace(
        ISO_639_2=0,
        LOGINFO=1,
        LOGDEBUG=0,
        LOGWARNING=2,
        NOTIFICATION_INFO=0,
        convertLanguage=lambda language, _format: language_codes.get(language.lower(), ''),
        getLocalizedString=lambda _identifier: 'Subtitles',
        executeJSONRPC=lambda _payload: '{}',
        log=lambda _message, _level: None,
    )
    xbmcgui = types.SimpleNamespace(
        Dialog=lambda: types.SimpleNamespace(notification=lambda *args: None))
    with mock.patch.dict(sys.modules, {'xbmc': xbmc, 'xbmcgui': xbmcgui}):
        spec = importlib.util.spec_from_file_location(
            'smart_subtitles_under_test', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class LanguageNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()

    def test_normalizes_display_names(self):
        self.assertEqual('eng', self.subtitles._to_iso639_2('English'))
        self.assertEqual('ita', self.subtitles._to_iso639_2('Italian'))
        self.assertEqual('por', self.subtitles._to_iso639_2('Portuguese'))

    def test_normalizes_iso_codes(self):
        self.assertEqual('eng', self.subtitles._to_iso639_2('en'))
        self.assertEqual('eng', self.subtitles._to_iso639_2('eng'))
        self.assertEqual('eng', self.subtitles._to_iso639_2('en_US'))

    def test_empty_and_unknown(self):
        self.assertEqual('', self.subtitles._to_iso639_2(''))
        self.assertEqual('', self.subtitles._to_iso639_2(None))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()

    def _run(self, streams, current, sub_enabled, preferred={'eng'},
             respect_lang=True):
        """Helper: run select_signs_songs_track with common mocks."""
        with mock.patch.object(self.subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams',
                                  return_value=(streams, current, sub_enabled)), \
                mock.patch.object(self.subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(self.subtitles, '_get_preferred_language_codes',
                                  return_value=preferred), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(self.subtitles, '_disable_subtitles') as dis_sub, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            return (
                self.subtitles.select_signs_songs_track(),
                set_sub,
                dis_sub,
            )

    def test_selects_english_forced_when_portuguese_comes_first(self):
        """The JJK bug: Portuguese (Forced) at idx 0, English Forced at idx 2.
        Must pick English, not Portuguese."""
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Brazilian (Forced)',
             'isforced': True},
            {'index': 1, 'language': 'por', 'name': 'Brazilian'},
            {'index': 2, 'language': 'eng', 'name': 'Forced',
             'isforced': True},
        ]
        result, set_sub, _ = self._run(streams, {'index': 0}, True)
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 2)

    def test_never_selects_a_non_preferred_forced_track(self):
        """If no preferred-language keyword track exists, don't pick a
        non-preferred one."""
        streams = [{'index': 0, 'language': 'ita', 'name': 'Italiano Forced'}]
        result, set_sub, _ = self._run(streams, {}, False)
        self.assertEqual('no_match', result)
        set_sub.assert_not_called()

    def test_disables_non_preferred_forced_then_selects_preferred(self):
        """Kodi auto-picked a non-preferred forced track. Disable it and
        select the preferred-language match."""
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Brazilian (Forced)',
             'isforced': True},
            {'index': 2, 'language': 'eng', 'name': 'Forced',
             'isforced': True},
        ]
        result, set_sub, dis_sub = self._run(streams, {'index': 0}, True)
        self.assertEqual('selected', result)
        dis_sub.assert_called_once_with(1)
        set_sub.assert_called_once_with(1, 2)

    def test_respects_active_preferred_track_at_index_zero(self):
        """If Kodi already has a preferred-language track active (even at
        index 0), don't override."""
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'English Forced',
             'isforced': True},
            {'index': 1, 'language': 'ita', 'name': 'Italiano Forced',
             'isforced': True},
        ]
        result, set_sub, _ = self._run(streams, {'index': 0}, True)
        self.assertEqual('skip', result)
        set_sub.assert_not_called()

    def test_respects_non_forced_non_preferred_as_user_choice(self):
        """A non-forced, non-preferred track that's active is likely a
        deliberate user choice — don't override."""
        streams = [
            {'index': 0, 'language': 'jpn', 'name': 'Japanese',
             'isforced': False},
            {'index': 1, 'language': 'eng', 'name': 'Forced',
             'isforced': True},
        ]
        result, set_sub, _ = self._run(streams, {'index': 0}, True)
        self.assertEqual('skip', result)
        set_sub.assert_not_called()

    def test_stale_index_does_not_trigger_skip(self):
        """If currentsubtitle.index doesn't resolve to a real stream in THIS
        file (stale carry-over from previous file), don't treat it as a
        user choice — proceed to scan."""
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'Forced',
             'isforced': True},
        ]
        # index=5 doesn't exist in streams → stale → should NOT skip
        result, set_sub, _ = self._run(streams, {'index': 5}, True)
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 0)

    def test_respect_language_false_ignores_language(self):
        """When respect_language is off, any keyword match is selected."""
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Brazilian (Forced)',
             'isforced': True},
        ]
        with mock.patch.object(self.subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams',
                                  return_value=(streams, {}, False)), \
                mock.patch.object(self.subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(self.subtitles, '_get_preferred_language_codes',
                                  return_value=set()), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            result = self.subtitles.select_signs_songs_track()
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 0)


class PreferredLanguageTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()

    def _codes(self, settings, ui_language=''):
        self.subtitles.xbmc.getLanguage = lambda _format, _region: ui_language
        with mock.patch.object(self.subtitles, '_get_global_setting',
                               side_effect=settings.get):
            return self.subtitles._get_preferred_language_codes()

    def test_uses_preferred_subtitle_language_first(self):
        self.assertEqual({'ita'}, self._codes({
            'locale.subtitlelanguage': 'Italian',
            'subtitles.languages': ['English'],
        }))

    def test_preferred_language_with_region_suffix(self):
        self.assertEqual({'por'}, self._codes({
            'locale.subtitlelanguage': 'Portuguese (Brazil)',
        }))

    def test_special_values_fall_back_to_download_languages(self):
        for special in ('original', 'default', 'forced_only', 'none', ''):
            self.assertEqual({'eng', 'spa'}, self._codes({
                'locale.subtitlelanguage': special,
                'subtitles.languages': ['English', 'Spanish'],
            }), special)

    def test_falls_back_to_ui_language(self):
        self.assertEqual({'eng'}, self._codes({
            'locale.subtitlelanguage': 'forced_only',
            'subtitles.languages': [],
        }, ui_language='ENG'))


class NotificationSettingTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()
        self.calls = []
        calls = self.calls
        self.subtitles.xbmcgui.NOTIFICATION_INFO = 0
        self.subtitles.xbmcgui.Dialog = lambda: types.SimpleNamespace(
            notification=lambda *args: calls.append(args))

    def test_notifies_by_default(self):
        with mock.patch.object(self.subtitles, '_get_setting', return_value=''):
            self.subtitles._notify_selected('English (Forced)')
        self.assertEqual(1, len(self.calls))

    def test_notification_can_be_disabled(self):
        with mock.patch.object(self.subtitles, '_get_setting', return_value='false'):
            self.subtitles._notify_selected('English (Forced)')
        self.assertEqual([], self.calls)


if __name__ == '__main__':
    unittest.main()
