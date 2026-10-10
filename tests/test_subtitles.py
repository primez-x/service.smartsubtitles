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
             respect_lang=True, keywords=('forced',), authoritative=None,
             should_stop=None):
        """Helper: run select_signs_songs_track with common mocks.
        ``authoritative`` defaults to ``preferred`` (i.e. the preference did
        not come only from Kodi's download-language list)."""
        if authoritative is None:
            authoritative = preferred
        with mock.patch.object(self.subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams',
                                  return_value=(streams, current, sub_enabled)), \
                mock.patch.object(self.subtitles, '_get_keywords',
                                  return_value=list(keywords)), \
                mock.patch.object(self.subtitles, '_get_preferred_languages',
                                  return_value=(preferred, authoritative)), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(self.subtitles, '_disable_subtitles') as dis_sub, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            return (
                self.subtitles.select_signs_songs_track(should_stop),
                set_sub,
                dis_sub,
            )

    def test_changes_nothing_once_the_file_is_no_longer_playing(self):
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Brazilian (Forced)',
             'isforced': True},
            {'index': 2, 'language': 'eng', 'name': 'Forced', 'isforced': True},
        ]
        result, set_sub, dis_sub = self._run(streams, {'index': 0}, True,
                                             should_stop=lambda: True)
        self.assertEqual('cancelled', result)
        set_sub.assert_not_called()
        dis_sub.assert_not_called()

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
                mock.patch.object(self.subtitles, '_get_preferred_languages',
                                  return_value=(set(), set())), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            result = self.subtitles.select_signs_songs_track()
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 0)


    # --- Kodi's isforced flag ---

    def test_forced_flag_selects_track_without_keyword_in_name(self):
        """Repro: Kodi auto-picked Portuguese forced at idx 0; the English
        forced track is just named "English". Must select idx 1."""
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Portuguese',
             'isforced': True},
            {'index': 1, 'language': 'eng', 'name': 'English',
             'isforced': True},
            {'index': 2, 'language': 'eng', 'name': 'English',
             'isforced': False},
        ]
        result, set_sub, _ = self._run(
            streams, {'index': 0}, True,
            keywords=('signs', 'songs', 'sign', 'forced'))
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 1)

    def test_forced_flag_selects_track_with_empty_name(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': '', 'isforced': False},
            {'index': 1, 'language': 'eng', 'name': '', 'isforced': True},
        ]
        result, set_sub, _ = self._run(streams, {}, False)
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 1)

    def test_forced_flag_beats_earlier_keyword_name(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'Signs & Songs'},
            {'index': 1, 'language': 'eng', 'name': 'English',
             'isforced': True},
        ]
        result, set_sub, _ = self._run(streams, {}, False,
                                       keywords=('signs', 'songs', 'forced'))
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 1)

    # --- No match never takes away what Kodi selected ---

    def test_no_match_never_disables_kodi_forced_pick(self):
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Portuguese',
             'isforced': True},
            {'index': 1, 'language': 'eng', 'name': 'English'},
        ]
        result, set_sub, dis_sub = self._run(streams, {'index': 0}, True)
        self.assertEqual('no_match', result)
        set_sub.assert_not_called()
        dis_sub.assert_not_called()

    def test_download_language_only_never_replaces_kodi_forced_pick(self):
        """Preferred languages came only from Kodi's (pre-filled) download
        languages: keep the forced track Kodi chose."""
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Portuguese',
             'isforced': True},
            {'index': 1, 'language': 'eng', 'name': 'English',
             'isforced': True},
        ]
        result, set_sub, dis_sub = self._run(
            streams, {'index': 0}, True, preferred={'eng'},
            authoritative=set())
        self.assertEqual('skip', result)
        set_sub.assert_not_called()
        dis_sub.assert_not_called()

    def test_replacement_uses_only_authoritative_languages(self):
        """forced_only + UI Portuguese + download English: an Italian forced
        pick is replaced by the Portuguese forced track, not the English
        one that came only from the download list."""
        streams = [
            {'index': 0, 'language': 'ita', 'name': 'Italiano',
             'isforced': True},
            {'index': 1, 'language': 'eng', 'name': 'English',
             'isforced': True},
            {'index': 2, 'language': 'por', 'name': 'Portuguese',
             'isforced': True},
        ]
        result, set_sub, _ = self._run(
            streams, {'index': 0}, True, preferred={'eng', 'por'},
            authoritative={'por'})
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 2)

    def test_forced_only_portuguese_ui_keeps_kodi_portuguese_forced(self):
        """End to end with real preference resolution: forced_only, Kodi
        pre-filled download language English, Portuguese UI. Kodi's
        Portuguese forced pick must stay."""
        settings = {'locale.subtitlelanguage': 'forced_only',
                    'subtitles.languages': ['English']}
        self.subtitles.xbmc.getLanguage = lambda _format, _region: 'por'
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'English',
             'isforced': True},
            {'index': 1, 'language': 'por', 'name': 'Portuguese',
             'isforced': True},
        ]
        with mock.patch.object(self.subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams',
                                  return_value=(streams, {'index': 1}, True)), \
                mock.patch.object(self.subtitles, '_get_keywords',
                                  return_value=['signs', 'songs', 'forced']), \
                mock.patch.object(self.subtitles, '_get_global_setting',
                                  side_effect=settings.get), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(self.subtitles, '_disable_subtitles') as dis_sub, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            result = self.subtitles.select_signs_songs_track()
        self.assertEqual('skip', result)
        set_sub.assert_not_called()
        dis_sub.assert_not_called()


class KeywordRankingTests(unittest.TestCase):
    KEYWORDS = ('signs', 'songs', 'sign', 'forced')  # legacy default

    def setUp(self):
        self.subtitles = load_subtitles_module()

    def _pick(self, names, keywords=KEYWORDS):
        streams = [{'index': i, 'language': 'eng', 'name': n}
                   for i, n in enumerate(names)]
        with mock.patch.object(self.subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams',
                                  return_value=(streams, {}, False)), \
                mock.patch.object(self.subtitles, '_get_keywords',
                                  return_value=list(keywords)), \
                mock.patch.object(self.subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(self.subtitles, '_notify_selected'):
            result = self.subtitles.select_signs_songs_track()
        if result != 'selected':
            return None
        return set_sub.call_args[0][1]

    def test_full_track_mentioning_signs_songs_loses(self):
        self.assertEqual(1, self._pick([
            'Full Subtitles (Dialogue + Signs & Songs)', 'Signs & Songs']))

    def test_full_track_alone_is_not_a_candidate(self):
        self.assertIsNone(self._pick(
            ['Full Subtitles (Dialogue + Signs & Songs)']))
        self.assertIsNone(self._pick(['English (Dialogue, Signs)']))

    def test_forced_dialogue_track_is_still_a_candidate(self):
        self.assertEqual(0, self._pick(['Forced (foreign dialogue only)']))

    def test_sign_does_not_match_inside_words(self):
        self.assertIsNone(self._pick(['English [Designed by GJM]']))

    def test_non_forced_is_not_forced(self):
        for name in ('English (Non-Forced)', 'English non forced',
                     'English NonForced', 'English [non_forced]'):
            self.assertIsNone(self._pick([name]), name)

    def test_non_forced_and_forced_in_same_list(self):
        self.assertEqual(1, self._pick(['English (Non-Forced)',
                                        'English (Forced)']))

    def test_signs_songs_rank_above_other_keywords(self):
        self.assertEqual(1, self._pick(['English (Forced)', 'Songs']))
        self.assertEqual(1, self._pick(['Forced', 'Sign']))

    def test_first_stream_wins_within_a_rank(self):
        self.assertEqual(0, self._pick(['Signs', 'Signs & Songs']))

    def test_custom_keywords_match_whole_words(self):
        self.assertEqual(1, self._pick(['Japanese', 'Lyrics [karaoke]'],
                                       keywords=('karaoke',)))
        self.assertIsNone(self._pick(['Karaokes Extra'], keywords=('karaoke',)))
        self.assertEqual(0, self._pick(['English [forced]'],
                                       keywords=('[forced]',)))

    def test_default_keywords_drop_bare_sign(self):
        with mock.patch.object(self.subtitles, '_get_setting', return_value=''):
            self.assertEqual(['signs', 'songs', 'forced'],
                             self.subtitles._get_keywords())
        # "signs" still matches a singular "Sign" track name
        self.assertEqual(0, self._pick(['Sign'], keywords=('signs',)))


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

    def test_forced_only_includes_ui_language(self):
        """Portuguese UI with Kodi's pre-filled English download language."""
        self.assertEqual({'eng', 'por'}, self._codes({
            'locale.subtitlelanguage': 'forced_only',
            'subtitles.languages': ['English'],
        }, ui_language='por'))

    def test_forced_only_authoritative_is_ui_language_only(self):
        self.subtitles.xbmc.getLanguage = lambda _format, _region: 'Portuguese'
        settings = {'locale.subtitlelanguage': 'forced_only',
                    'subtitles.languages': ['English']}
        with mock.patch.object(self.subtitles, '_get_global_setting',
                               side_effect=settings.get):
            self.assertEqual(({'eng', 'por'}, {'por'}),
                             self.subtitles._get_preferred_languages())

    def test_download_languages_are_not_authoritative(self):
        self.subtitles.xbmc.getLanguage = lambda _format, _region: 'por'
        settings = {'locale.subtitlelanguage': 'original',
                    'subtitles.languages': ['English']}
        with mock.patch.object(self.subtitles, '_get_global_setting',
                               side_effect=settings.get):
            self.assertEqual(({'eng'}, set()),
                             self.subtitles._get_preferred_languages())

    def test_ui_language_fallback_is_normalized(self):
        self.assertEqual({'por'}, self._codes({
            'locale.subtitlelanguage': 'none',
            'subtitles.languages': [],
        }, ui_language='Portuguese'))

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


class PlexKodiConnectWarningTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()
        self.logs = []
        self.subtitles.xbmc.log = lambda msg, level: self.logs.append((msg, level))
        self.conditions = []

    def _check(self, enabled=True, pick='0', calls=1, version='3.12.59'):
        def cond(expr):
            self.conditions.append(expr)
            return enabled
        self.subtitles.xbmc.getCondVisibility = cond
        addon = types.SimpleNamespace(
            getSetting=lambda key: {'subtitleStreamPick': pick}.get(key, ''),
            getAddonInfo=lambda key: version if key == 'version' else '')
        xbmcaddon = types.SimpleNamespace(
            Addon=lambda addon_id: addon
            if addon_id == 'plugin.video.plexkodiconnect' else None)
        with mock.patch.dict(sys.modules, {'xbmcaddon': xbmcaddon}):
            for _ in range(calls):
                self.subtitles.warn_plexkodiconnect_interop()
        return [m for m, level in self.logs
                if level == self.subtitles.xbmc.LOGWARNING]

    def test_warns_once_when_pkc_picks_plex_subtitles(self):
        warnings = self._check(calls=3)
        self.assertEqual(1, len(warnings))
        self.assertIn('subtitleStreamPick', warnings[0])
        self.assertEqual(
            ['System.AddonIsEnabled(plugin.video.plexkodiconnect)'],
            self.conditions)

    def test_no_warning_when_pkc_defers_to_this_addon(self):
        self.assertEqual([], self._check(version='3.12.60'))

    def test_no_warning_when_pkc_lets_kodi_pick(self):
        self.assertEqual([], self._check(pick='1'))

    def test_no_warning_when_pkc_disabled(self):
        self.assertEqual([], self._check(enabled=False))

    def test_setting_read_errors_are_ignored(self):
        self.subtitles.xbmc.getCondVisibility = lambda _expr: True

        def boom(_addon_id):
            raise RuntimeError('Unknown addon id')
        with mock.patch.dict(sys.modules, {
                'xbmcaddon': types.SimpleNamespace(Addon=boom)}):
            self.subtitles.warn_plexkodiconnect_interop()
        self.assertEqual([], self.logs)


if __name__ == '__main__':
    unittest.main()
