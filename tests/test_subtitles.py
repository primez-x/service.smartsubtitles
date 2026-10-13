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


class LanguageFallbackTests(unittest.TestCase):
    """The static table is used when Kodi's converter fails or is unhelpful."""

    def setUp(self):
        self.subtitles = load_subtitles_module()

    def use_converter(self, convert):
        self.subtitles.xbmc.convertLanguage = convert

    def test_converter_returning_nothing_uses_table(self):
        self.use_converter(lambda _language, _format: '')
        for name, code in (('Norwegian', 'nor'), ('Greek', 'gre'),
                           ('Portuguese (Brazil)', 'por'), ('pt-BR', 'por'),
                           ('PT_br', 'por'), ('Chinese (Traditional)', 'chi')):
            self.assertEqual(code, self.subtitles._to_iso639_2(name), name)

    def test_converter_raising_uses_table(self):
        def broken(_language, _format):
            raise TypeError('unsupported')
        self.use_converter(broken)
        self.assertEqual('por', self.subtitles._to_iso639_2('Portuguese (Brazil)'))
        self.use_converter(lambda *_args: (_ for _ in ()).throw(AttributeError()))
        self.assertEqual('ukr', self.subtitles._to_iso639_2('Ukrainian'))

    def test_bibliographic_and_terminologic_codes_match(self):
        self.use_converter(lambda _language, _format: '')
        for pair in (('ger', 'deu'), ('fre', 'fra'), ('dut', 'nld'),
                     ('chi', 'zho'), ('cze', 'ces'), ('gre', 'ell'),
                     ('German', 'deu'), ('French', 'fre')):
            self.assertEqual(self.subtitles._to_iso639_2(pair[0]),
                             self.subtitles._to_iso639_2(pair[1]), pair)

    def test_nynorsk_stays_distinct_from_bokmal(self):
        self.use_converter(lambda _language, _format: '')
        for name in ('nn', 'nno', 'Norwegian Nynorsk'):
            self.assertEqual('nno', self.subtitles._to_iso639_2(name), name)
        self.assertNotEqual(self.subtitles._to_iso639_2('nob'),
                            self.subtitles._to_iso639_2('nno'))

    def test_converter_terminologic_result_is_canonicalized(self):
        self.use_converter(lambda language, _format: {
            'german': 'deu', 'ger': 'ger', 'fra': 'fra'}.get(language, ''))
        self.assertEqual('ger', self.subtitles._to_iso639_2('German'))
        self.assertEqual('ger', self.subtitles._to_iso639_2('ger'))
        self.assertEqual('fre', self.subtitles._to_iso639_2('fra'))

    def test_converter_gets_whole_value_when_base_fails(self):
        self.use_converter(lambda language, _format: {
            'serbo-croatian': 'hbs'}.get(language, ''))
        self.assertEqual('hbs', self.subtitles._to_iso639_2('Serbo-Croatian'))

    def test_unknown_language_is_kept_lowercase(self):
        self.use_converter(lambda _language, _format: '')
        self.assertEqual('klingon', self.subtitles._to_iso639_2('Klingon'))


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()
        self.notifications = []

    def _run(self, streams, current, sub_enabled, preferred={'eng'},
             respect_lang=True, keywords=('forced',), authoritative=None,
             should_stop=None, set_result=True):
        """Helper: run select_signs_songs_track with common mocks.
        ``authoritative`` defaults to ``preferred`` (i.e. the preference did
        not come only from Kodi's download-language list)."""
        if authoritative is None:
            authoritative = preferred
        state = {'streams': list(streams), 'current': dict(current),
                 'enabled': sub_enabled}

        def get_streams(_playerid):
            return (state['streams'], state['current'], state['enabled'])

        def set_subtitle(_playerid, index, _should_stop=None):
            if not set_result:
                return False
            stream = self.subtitles._stream_for_index(state['streams'], index)
            state['current'] = dict(stream or {'index': index})
            state['enabled'] = True
            return True

        def disable_subtitles(_playerid, _should_stop=None):
            state['current'] = {}
            state['enabled'] = False
            return True

        def get_setting_bool(key, default=False):
            if key == 'respect_language':
                return respect_lang
            return True

        with mock.patch.object(self.subtitles, '_get_setting_bool',
                               side_effect=get_setting_bool), \
                mock.patch.object(self.subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams',
                                  side_effect=get_streams), \
                mock.patch.object(self.subtitles, '_get_keywords',
                                  return_value=list(keywords)), \
                mock.patch.object(self.subtitles, '_get_preferred_languages',
                                  return_value=(preferred, authoritative)), \
                mock.patch.object(self.subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(self.subtitles, '_disable_subtitles') as dis_sub, \
                mock.patch.object(self.subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            set_sub.side_effect = set_subtitle
            dis_sub.side_effect = disable_subtitles
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

    def test_failed_set_response_does_not_report_selection(self):
        streams = [{'index': 0, 'language': 'eng', 'name': 'Forced'}]
        result, set_sub, _dis_sub = self._run(
            streams, {}, False, set_result=False)
        self.assertEqual('retry', result)
        set_sub.assert_called_once_with(1, 0, None)
        self.assertEqual([], self.notifications)

    def test_stream_index_shift_is_resolved_before_setting(self):
        initial = [
            {'index': 0, 'language': 'eng', 'name': 'English'},
            {'index': 1, 'language': 'eng', 'name': 'Signs'},
        ]
        shifted = [
            {'index': 0, 'language': 'eng', 'name': 'English'},
            {'index': 1, 'language': 'spa', 'name': 'Spanish'},
            {'index': 2, 'language': 'eng', 'name': 'Signs'},
        ]
        subtitles = self.subtitles
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=[
                                      (initial, {}, False),
                                      (shifted, {}, False),
                                      (shifted, {'index': 2}, True)]), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['signs']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  return_value=True) as set_sub, \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            result = subtitles.select_signs_songs_track()
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 2, None)
        self.assertEqual(['Signs'], self.notifications)

    def test_late_user_subtitle_choice_is_not_overridden(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'English'},
            {'index': 1, 'language': 'eng', 'name': 'Forced',
             'isforced': True},
        ]
        snapshots = [
            (streams, {}, False),
            (streams, {'index': 0}, True),
        ]
        subtitles = self.subtitles
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=snapshots), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle') as set_sub, \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            result = subtitles.select_signs_songs_track()
        self.assertEqual('skip', result)
        set_sub.assert_not_called()
        self.assertEqual([], self.notifications)

    def test_initially_disabled_current_stream_does_not_block_selection(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'English'},
            {'index': 1, 'language': 'eng', 'name': 'Forced',
             'isforced': True},
        ]
        result, set_sub, _dis_sub = self._run(
            streams, {'index': 0}, False)
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 1, None)

    def test_manual_off_transition_across_retries_is_respected(self):
        streams = [{'index': 0, 'language': 'eng', 'name': 'Forced'}]
        snapshots = [
            ([], {'index': 5}, True),
            (streams, {'index': 5}, False),
        ]
        snapshot_index = []

        def get_streams(_playerid):
            snapshot_index.append(True)
            return snapshots[min(len(snapshot_index) - 1, 1)]

        subtitles = self.subtitles
        attempt_state = {}
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=get_streams), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle') as set_sub:
            first_result = subtitles.select_signs_songs_track(
                None, attempt_state)
            second_result = subtitles.select_signs_songs_track(
                None, attempt_state)

        self.assertEqual('retry', first_result)
        self.assertEqual('skip', second_result)
        set_sub.assert_not_called()

    def test_manual_off_during_fresh_read_is_respected(self):
        streams = [{'index': 0, 'language': 'eng', 'name': 'Forced'}]
        snapshots = [
            (streams, {'index': 5}, True),
            (streams, {'index': 5}, False),
        ]
        snapshot_index = []

        def get_streams(_playerid):
            snapshot_index.append(True)
            return snapshots[min(len(snapshot_index) - 1, 1)]

        subtitles = self.subtitles
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=get_streams), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle') as set_sub:
            result = subtitles.select_signs_songs_track()

        self.assertEqual('skip', result)
        set_sub.assert_not_called()

    def test_delayed_readback_confirms_selection_on_next_call(self):
        streams = [{'index': 1, 'language': 'eng', 'name': 'Forced'}]
        snapshots = [
            (streams, {}, False),
            (streams, {}, False),
            (streams, {}, False),
            (streams, {'index': 1}, True),
        ]
        subtitles = self.subtitles
        attempt_state = {}
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=snapshots), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  return_value=True) as set_sub, \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            first_result = subtitles.select_signs_songs_track(
                None, attempt_state)
            second_result = subtitles.select_signs_songs_track(
                None, attempt_state)

        self.assertEqual('retry', first_result)
        self.assertEqual('selected', second_result)
        set_sub.assert_called_once_with(1, 1, None)
        self.assertEqual(['Forced'], self.notifications)

    def test_manual_off_after_set_is_not_reenabled_on_retry(self):
        streams = [{'index': 1, 'language': 'eng', 'name': 'Forced'}]
        snapshots = [
            (streams, {}, False),
            (streams, {}, False),
            (streams, {}, False),
            (streams, {'index': 1}, False),
        ]
        subtitles = self.subtitles
        attempt_state = {}
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=snapshots), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  return_value=True) as set_sub, \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            first_result = subtitles.select_signs_songs_track(
                None, attempt_state)
            second_result = subtitles.select_signs_songs_track(
                None, attempt_state)

        self.assertEqual('retry', first_result)
        self.assertEqual('skip', second_result)
        set_sub.assert_called_once_with(1, 1, None)
        self.assertEqual([], self.notifications)

    def test_cancelled_retry_does_not_confirm_pending_selection(self):
        streams = [{'index': 1, 'language': 'eng', 'name': 'Forced'}]
        snapshots = [
            (streams, {}, False),
            (streams, {}, False),
            (streams, {}, False),
            (streams, {'index': 1}, True),
        ]
        subtitles = self.subtitles
        attempt_state = {}
        stop_checks = []

        def should_stop():
            stop_checks.append(True)
            return len(stop_checks) > 1

        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=snapshots), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  return_value=True), \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            first_result = subtitles.select_signs_songs_track(
                None, attempt_state)
            second_result = subtitles.select_signs_songs_track(
                should_stop, attempt_state)

        self.assertEqual('retry', first_result)
        self.assertEqual('cancelled', second_result)
        self.assertEqual([], self.notifications)

    def test_changed_player_does_not_confirm_pending_selection(self):
        streams = [{'index': 1, 'language': 'eng', 'name': 'Forced'}]
        snapshots = [
            (streams, {}, False),
            (streams, {}, False),
            (streams, {}, False),
            (streams, {'index': 1}, True),
        ]
        active = {'player_id': 1}
        read_count = []

        def get_streams(_playerid):
            read_count.append(True)
            if len(read_count) == 4:
                active['player_id'] = 2
            return snapshots[len(read_count) - 1]

        subtitles = self.subtitles
        attempt_state = {}
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  side_effect=lambda: active['player_id']), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=get_streams), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  return_value=True), \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            first_result = subtitles.select_signs_songs_track(
                None, attempt_state)
            second_result = subtitles.select_signs_songs_track(
                None, attempt_state)

        self.assertEqual('retry', first_result)
        self.assertEqual('retry', second_result)
        self.assertEqual([], self.notifications)

    def test_readback_mismatch_does_not_report_selection(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'English'},
            {'index': 1, 'language': 'eng', 'name': 'Forced'},
        ]
        subtitles = self.subtitles
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=[
                                      (streams, {}, False),
                                      (streams, {}, False),
                                      (streams, {'index': 0}, True)]), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  return_value=True) as set_sub, \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            result = subtitles.select_signs_songs_track()
        self.assertEqual('retry', result)
        set_sub.assert_called_once_with(1, 1, None)
        self.assertEqual([], self.notifications)

    def test_disabled_readback_does_not_report_selection(self):
        streams = [{'index': 0, 'language': 'eng', 'name': 'Forced'}]
        subtitles = self.subtitles
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  side_effect=[
                                      (streams, {}, False),
                                      (streams, {}, False),
                                      (streams, {'index': 0}, False)]), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  return_value=True) as set_sub, \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            result = subtitles.select_signs_songs_track()
        self.assertEqual('retry', result)
        set_sub.assert_called_once_with(1, 0, None)
        self.assertEqual([], self.notifications)

    def test_cancel_after_setting_prevents_notification(self):
        streams = [{'index': 0, 'language': 'eng', 'name': 'Forced'}]
        cancelled = {'value': False}

        def set_subtitle(_playerid, _index, _should_stop=None):
            cancelled['value'] = True
            return True

        subtitles = self.subtitles
        with mock.patch.object(subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(subtitles, '_get_subtitle_streams',
                                  return_value=(streams, {}, False)), \
                mock.patch.object(subtitles, '_get_keywords',
                                  return_value=['forced']), \
                mock.patch.object(subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(subtitles, '_set_subtitle',
                                  side_effect=set_subtitle), \
                mock.patch.object(subtitles, '_notify_selected',
                                  side_effect=self.notifications.append):
            result = subtitles.select_signs_songs_track(
                lambda: cancelled['value'])
        self.assertEqual('cancelled', result)
        self.assertEqual([], self.notifications)


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
        set_sub.assert_called_once_with(1, 2, None)

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
        dis_sub.assert_called_once_with(1, None)
        set_sub.assert_called_once_with(1, 2, None)

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
        set_sub.assert_called_once_with(1, 0, None)

    def test_respect_language_false_ignores_language(self):
        """When respect_language is off, any keyword match is selected."""
        streams = [
            {'index': 0, 'language': 'por', 'name': 'Brazilian (Forced)',
             'isforced': True},
        ]
        result, set_sub, _dis_sub = self._run(
            streams, {}, False, preferred=set(), authoritative=set(),
            respect_lang=False, keywords=('forced',))
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 0, None)


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
        set_sub.assert_called_once_with(1, 1, None)

    def test_forced_flag_selects_track_with_empty_name(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': '', 'isforced': False},
            {'index': 1, 'language': 'eng', 'name': '', 'isforced': True},
        ]
        result, set_sub, _ = self._run(streams, {}, False)
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 1, None)

    def test_forced_flag_beats_earlier_keyword_name(self):
        streams = [
            {'index': 0, 'language': 'eng', 'name': 'Signs & Songs'},
            {'index': 1, 'language': 'eng', 'name': 'English',
             'isforced': True},
        ]
        result, set_sub, _ = self._run(streams, {}, False,
                                       keywords=('signs', 'songs', 'forced'))
        self.assertEqual('selected', result)
        set_sub.assert_called_once_with(1, 1, None)

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
        set_sub.assert_called_once_with(1, 2, None)

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


class SetSubtitleCommandTests(unittest.TestCase):
    def setUp(self):
        self.subtitles = load_subtitles_module()

    def test_set_subtitle_checks_both_jsonrpc_responses(self):
        cases = (
            ([{'error': {'code': -1}}, {'result': 'OK'}], 1),
            ([{'result': 'OK'}, {'error': {'code': -1}}], 2),
        )
        for responses, call_count in cases:
            with self.subTest(responses=responses), \
                    mock.patch.object(self.subtitles, '_jsonrpc',
                                      side_effect=responses) as jsonrpc:
                result = self.subtitles._set_subtitle(1, 3)
            self.assertEqual(call_count, jsonrpc.call_count)
            self.assertIs(result, False)

    def test_set_subtitle_reports_success_only_after_both_requests_succeed(self):
        with mock.patch.object(self.subtitles, '_jsonrpc',
                               side_effect=[{'result': 'OK'},
                                            {'result': 'OK'}]) as jsonrpc:
            result = self.subtitles._set_subtitle(1, 3)
        self.assertIs(result, True)
        self.assertEqual(2, jsonrpc.call_count)

    def test_set_subtitle_stops_when_playback_changes_between_requests(self):
        calls = []
        stopped = {'value': False}

        def jsonrpc(_method, _params):
            calls.append(True)
            stopped['value'] = True
            return {'result': 'OK'}

        with mock.patch.object(self.subtitles, '_jsonrpc', side_effect=jsonrpc):
            result = self.subtitles._set_subtitle(
                1, 3, lambda: stopped['value'])
        self.assertIs(result, False)
        self.assertEqual(1, len(calls))


class KeywordRankingTests(unittest.TestCase):
    KEYWORDS = ('signs', 'songs', 'sign', 'forced')  # legacy default

    def setUp(self):
        self.subtitles = load_subtitles_module()

    def _pick(self, names, keywords=KEYWORDS):
        streams = [{'index': i, 'language': 'eng', 'name': n}
                   for i, n in enumerate(names)]
        state = {'current': {}, 'enabled': False}

        def get_streams(_playerid):
            return streams, state['current'], state['enabled']

        def set_subtitle(_playerid, index, _should_stop=None):
            state['current'] = self.subtitles._stream_for_index(streams, index)
            state['enabled'] = True
            return True

        with mock.patch.object(self.subtitles, '_get_setting_bool',
                               return_value=True), \
                mock.patch.object(self.subtitles, '_get_active_player_id',
                                  return_value=1), \
                mock.patch.object(self.subtitles, '_get_subtitle_streams',
                                  side_effect=get_streams), \
                mock.patch.object(self.subtitles, '_get_keywords',
                                  return_value=list(keywords)), \
                mock.patch.object(self.subtitles, '_get_preferred_languages',
                                  return_value=({'eng'}, {'eng'})), \
                mock.patch.object(self.subtitles, '_set_subtitle',
                                  side_effect=set_subtitle) as set_sub, \
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

    def test_empty_keywords_disable_name_matching(self):
        for raw in ('', None):
            with self.subTest(raw=raw), \
                    mock.patch.object(self.subtitles, '_get_setting',
                                      return_value=raw):
                keywords = self.subtitles._get_keywords()
            self.assertEqual([], keywords)
        self.assertIsNone(self.subtitles._candidate_rank(
            {'name': 'Forced'}, keywords))
        self.assertEqual(self.subtitles.RANK_FORCED_FLAG,
                         self.subtitles._candidate_rank(
                             {'name': 'English', 'isforced': True}, keywords))


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
