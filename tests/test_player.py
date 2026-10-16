import importlib.util
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest import mock

from test_corrections import load_corrections_module
from test_subtitles import load_subtitles_module


SOURCE = Path(__file__).parents[1] / 'resources' / 'lib' / 'player.py'


class FakePlayer(object):
    def __init__(self):
        self.playing_file = 'episode1.mkv'
        self.get_playing_file_calls = 0

    def isPlaying(self):
        return self.playing_file is not None

    def getPlayingFile(self):
        self.get_playing_file_calls += 1
        if self.playing_file is None:
            raise RuntimeError('not playing')
        return self.playing_file


class FakeMonitor(object):
    aborted = False

    def abortRequested(self):
        return FakeMonitor.aborted


class FakeWindow(object):
    def __init__(self):
        self.properties = {}

    def setProperty(self, key, value):
        self.properties[key] = value


HOME = FakeWindow()


class FakeClock(object):
    def __init__(self):
        self.elapsed = 0.0

    def monotonic(self):
        return self.elapsed


class AdvancingEvent(object):
    def __init__(self, clock):
        self.clock = clock

    def is_set(self):
        return False

    def wait(self, seconds):
        self.clock.elapsed += seconds
        return False


def load_player_module(select, subtitle_module=None, corrections_module=None):
    xbmc = types.SimpleNamespace(
        LOGDEBUG=0,
        LOGINFO=1,
        Player=FakePlayer,
        Monitor=FakeMonitor,
        log=lambda _message, _level: None,
    )
    xbmc.LOGERROR = 4
    xbmc.LOGWARNING = 2
    subtitles = subtitle_module or types.SimpleNamespace(
        select_signs_songs_track=select,
        _get_playback_snapshot=lambda: None,
        _get_playing_item=lambda _playerid: {},
        _get_setting_bool=lambda _key, default=True: False,
        notify_selection_failure=lambda _result: None)
    xbmcgui = types.SimpleNamespace(Window=lambda _window_id: HOME)
    with mock.patch.dict(sys.modules, {'xbmc': xbmc, 'xbmcgui': xbmcgui,
                                       'subtitles': subtitles,
                                       'corrections': corrections_module or load_corrections_module()}):
        spec = importlib.util.spec_from_file_location(
            'smart_subtitles_player_under_test', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class RetryTests(unittest.TestCase):
    def setUp(self):
        FakeMonitor.aborted = False
        HOME.properties.clear()
        self.calls = []

    def _player(self, results=None, interval=0.01):
        results = list(results or ['retry'])

        def select(should_stop=None, _attempt_state=None):
            self.calls.append(should_stop)
            result = results.pop(0) if len(results) > 1 else results[0]
            if isinstance(result, Exception):
                raise result
            return result

        module = load_player_module(select)
        module.RETRY_INTERVAL = interval
        self.module = module
        return module.SmartSubtitlesPlayer()

    def _clock_player(self, select, interval=1.0):
        clock = FakeClock()
        module = load_player_module(select)
        module.RETRY_INTERVAL = interval
        module.time = types.SimpleNamespace(monotonic=clock.monotonic)
        self.module = module
        return module.SmartSubtitlesPlayer(), clock, AdvancingEvent(clock)

    def test_stops_retrying_once_selection_finishes(self):
        player = self._player(['retry', 'selected'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual(2, len(self.calls))

    def test_publishes_the_result_for_the_playing_file(self):
        player = self._player(['retry', 'no_match'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual('no_match|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_skip_is_final_only_when_repeated(self):
        # The first read can still be the previous episode's subtitle.
        player = self._player(['skip', 'selected'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual(2, len(self.calls))
        self.assertEqual('selected|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

        self.calls[:] = []
        player = self._player(['skip', 'skip', 'selected'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual(2, len(self.calls))
        self.assertEqual('skip|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_selection_is_told_when_to_stop(self):
        player = self._player(['selected'])
        cancel = threading.Event()
        player._select_with_retry(cancel, 'episode1.mkv')
        should_stop = self.calls[0]
        self.assertFalse(should_stop())
        player.playing_file = 'episode2.mkv'
        self.assertTrue(should_stop())

    def test_cancelled_selection_publishes_nothing_final(self):
        player = self._player(['cancelled'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual('pending|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_retries_after_transient_selection_error(self):
        player = self._player([ValueError('bad reply'), 'selected'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual(2, len(self.calls))
        self.assertEqual('selected|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_retries_when_video_player_is_not_ready(self):
        player = self._player(['no_player', 'selected'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual(2, len(self.calls))
        self.assertEqual('selected|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_audio_only_is_final_after_one_attempt(self):
        player = self._player(['audio_only', 'selected'])
        player.playing_file = 'song.mp3'
        player._select_with_retry(threading.Event(), 'song.mp3')
        self.assertEqual(1, len(self.calls))
        self.assertEqual('audio_only|song.mp3', HOME.properties['SmartSubtitles.Result'])

    def test_current_file_avoids_get_playing_file_when_stopped(self):
        player = self._player(['selected'])
        player.playing_file = None
        player.get_playing_file_calls = 0
        self.assertIsNone(player._current_file())
        self.assertEqual(0, player.get_playing_file_calls)

    def test_retries_for_streams_arriving_after_two_seconds(self):
        player_state = {}

        def select(_should_stop=None, _attempt_state=None):
            clock = player_state['clock']
            self.calls.append(clock.elapsed)
            return 'selected' if clock.elapsed >= 2.5 else 'retry'

        player, clock, cancel = self._clock_player(select)
        player_state['clock'] = clock
        player._select_with_retry(cancel, 'episode1.mkv')

        self.assertEqual('selected|episode1.mkv', HOME.properties['SmartSubtitles.Result'])
        self.assertGreaterEqual(self.calls[-1], 2.5)
        self.assertLess(clock.elapsed, self.module.RETRY_WINDOW)

    def test_reuses_selection_state_across_retries(self):
        states = []

        def select(_should_stop=None, attempt_state=None):
            states.append(attempt_state)
            if len(states) == 1:
                attempt_state['pending'] = True
                return 'retry'
            return 'selected'

        module = load_player_module(select)
        module.RETRY_INTERVAL = 0.001
        player = module.SmartSubtitlesPlayer()
        player._select_with_retry(threading.Event(), 'episode1.mkv')

        self.assertEqual(2, len(states))
        self.assertIs(states[0], states[1])
        self.assertEqual('selected|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_manual_skip_after_two_seconds_stops_retries(self):
        player_state = {}

        def select(_should_stop=None, _attempt_state=None):
            clock = player_state['clock']
            self.calls.append(clock.elapsed)
            return 'skip' if clock.elapsed >= 2.5 else 'retry'

        player, clock, cancel = self._clock_player(select)
        player_state['clock'] = clock
        player._select_with_retry(cancel, 'episode1.mkv')

        self.assertEqual('skip|episode1.mkv', HOME.properties['SmartSubtitles.Result'])
        self.assertEqual(5, len(self.calls))
        self.assertEqual([3.0, 4.0], self.calls[-2:])

    def test_playback_change_after_two_seconds_cancels_retries(self):
        state = {}

        def select(_should_stop=None, _attempt_state=None):
            clock = state['clock']
            player = state['player']
            self.calls.append(clock.elapsed)
            if clock.elapsed >= 2.5:
                player.playing_file = 'episode2.mkv'
                return 'selected'
            return 'retry'

        player, clock, cancel = self._clock_player(select)
        state['clock'] = clock
        state['player'] = player
        player._select_with_retry(cancel, 'episode1.mkv')

        self.assertGreaterEqual(self.calls[-1], 2.5)
        self.assertEqual('pending|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_retry_deadline_expires_after_ten_seconds(self):
        player, clock, cancel = self._clock_player(
            lambda _should_stop=None, _attempt_state=None:
                self.calls.append('retry') or 'retry')
        player._select_with_retry(cancel, 'episode1.mkv')

        self.assertEqual(10.0, self.module.RETRY_WINDOW)
        self.assertEqual(10.0, clock.elapsed)
        self.assertEqual(10, len(self.calls))
        self.assertEqual('no_streams|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_retry_wait_does_not_extend_deadline(self):
        player, clock, cancel = self._clock_player(
            lambda _should_stop=None, _attempt_state=None:
                self.calls.append('retry') or 'retry',
            interval=6.0)
        player._select_with_retry(cancel, 'episode1.mkv')

        self.assertEqual(10.0, clock.elapsed)
        self.assertEqual(2, len(self.calls))
        self.assertEqual('no_streams|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_repeated_errors_stop_within_the_retry_window(self):
        player = self._player([ValueError('bad reply')], interval=0.005)
        self.module.RETRY_WINDOW = 0.03
        started = time.monotonic()
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        elapsed = time.monotonic() - started
        self.assertGreater(len(self.calls), 1)
        self.assertLess(elapsed, 0.1)
        self.assertEqual('error|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_playback_change_during_selection_does_not_publish_stale_result(self):
        holder = {}

        def select(should_stop=None, _attempt_state=None):
            holder['player'].playing_file = 'episode2.mkv'
            return 'no_match'

        player_module = load_player_module(select)
        guarded_player = player_module.SmartSubtitlesPlayer()
        holder['player'] = guarded_player
        guarded_player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual('pending|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_cancelled_attempt_does_not_select(self):
        player = self._player()
        cancel = threading.Event()
        cancel.set()
        player._select_with_retry(cancel, 'episode1.mkv')
        self.assertEqual([], self.calls)

    def test_cancel_timer_stops_in_flight_retry(self):
        player = self._player(interval=5)
        player.onAVStarted()
        thread = player._timer
        player.onPlayBackStopped()
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(1, len(self.calls))

    def test_new_playback_cancels_previous_attempt(self):
        player = self._player(interval=5)
        player.onAVStarted()
        first_thread = player._timer
        first_cancel = player._cancel_event
        player.onAVStarted()
        self.assertTrue(first_cancel.is_set())
        self.assertFalse(player._cancel_event.is_set())
        first_thread.join(1)
        self.assertFalse(first_thread.is_alive())
        player.onPlayBackEnded()
        player._timer = None

    def test_stops_when_kodi_aborts(self):
        player = self._player()
        FakeMonitor.aborted = True
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual([], self.calls)

    def test_stops_when_a_different_file_is_playing(self):
        player = self._player()
        player.playing_file = 'episode2.mkv'
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual([], self.calls)

    def test_stops_when_nothing_is_playing(self):
        player = self._player()
        player.playing_file = None
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual([], self.calls)


class PlaybackObservationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.subtitles = load_subtitles_module()
        self.corrections = load_corrections_module()
        self.clock = FakeClock()
        self.streams = [{'index': 0, 'name': 'Signs', 'language': 'eng'},
                        {'index': 1, 'name': 'English', 'language': 'eng'}]
        self.current = {}
        self.enabled = False
        self.audio = {'index': 0, 'language': 'eng', 'name': 'English'}
        self.writes = []
        self.settings = {'enabled': True, 'respect_language': True,
                         'remember_manual': True, 'notify_selected': False}
        self.module = load_player_module(None, self.subtitles, self.corrections)
        self.module.time = self.clock
        self.player = self.module.SmartSubtitlesPlayer()
        self.player._memory = self.corrections.CorrectionMemory(
            Path(self.temp.name) / 'manual-corrections.json')
        self.subtitles._jsonrpc = self.rpc
        self.subtitles._get_setting_bool = lambda key, default=True: self.settings.get(key, default)
        self.subtitles._get_preferred_languages = lambda: ({'eng'}, {'eng'})
        self.subtitles._get_keywords = lambda: ['signs', 'forced']
        self.subtitles._notify_confirmed_selection = lambda _name: None
        self.subtitles.notify_selection_failure = lambda _result: None
        self.addCleanup(self.player._cancel_timer)

    def rpc(self, method, params=None):
        if method == 'Player.GetActivePlayers':
            return {'result': [{'type': 'video', 'playerid': 1}]}
        if method == 'Player.GetProperties':
            return {'result': {'subtitles': list(self.streams),
                'currentsubtitle': dict(self.current), 'subtitleenabled': self.enabled,
                'currentaudiostream': dict(self.audio)}}
        if method == 'Player.GetItem':
            return {'result': {'item': {'type': 'episode', 'tvshowid': 17,
                                      'showtitle': 'Show',
                                      'file': self.player.playing_file}}}
        if method == 'Player.SetSubtitle':
            value = params['subtitle']
            self.writes.append(value)
            if type(value) is int:
                self.current = dict(next(s for s in self.streams if s['index'] == value))
            else:
                self.enabled = value == 'on'
            self.player.onAVChange()
            return {'result': 'OK'}
        raise AssertionError(method)

    def start(self):
        self.player.onAVStarted()
        self.player._timer.join(1)
        self.assertFalse(self.player._timer.is_alive())

    def poll(self, seconds=1):
        self.clock.elapsed += seconds
        self.player.poll()
        if self.player._timer is not None:
            self.player._timer.join(1)

    def test_own_selection_callbacks_do_not_reselect_or_record(self):
        self.start()
        for _ in range(5):
            self.player.onAVChange()
            self.poll()
        self.assertEqual([0, 'on'], self.writes)
        self.assertFalse((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_callback_bypasses_quiet_poll_interval(self):
        self.start()
        self.poll()
        self.enabled = False
        self.player.onAVChange()
        self.poll(0.1)
        self.player.onAVChange()
        self.poll(0.8)
        self.assertTrue((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_quiet_polls_coalesce_until_next_observation(self):
        self.start()
        self.poll()
        self.enabled = False
        self.poll(0.1)
        self.poll(0.8)
        self.assertFalse((Path(self.temp.name) / 'manual-corrections.json').exists())
        self.poll(0.2)
        self.poll(1.1)
        self.assertTrue((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_late_streams_restart_after_original_window(self):
        self.streams = [{'index': 1, 'name': 'English', 'language': 'eng'}]
        self.start()
        self.clock.elapsed = 30
        self.streams.append({'index': 2, 'name': 'Signs', 'language': 'eng'})
        self.player.onAVChange()
        self.poll()
        self.poll()
        self.assertEqual([2, 'on'], self.writes)

    def test_audio_change_rechecks_but_keeps_existing_user_choice(self):
        self.current = dict(self.streams[1])
        self.enabled = True
        self.start()
        self.audio['language'] = 'jpn'
        self.player.onAVChange()
        self.poll()
        self.poll()
        self.assertEqual([], self.writes)

    def test_audio_change_restarts_when_no_choice_was_made(self):
        self.streams = [{'index': 1, 'name': 'Forced', 'language': 'eng'}]
        self.subtitles._get_keywords = lambda: []
        self.start()
        self.subtitles._get_keywords = lambda: ['forced']
        self.audio['language'] = 'jpn'
        self.player.onAVChange()
        self.poll()
        self.poll()
        self.assertEqual([1, 'on'], self.writes)

    def test_stream_append_keeps_manual_observer_for_own_active_choice(self):
        self.start()
        self.streams.append({'index': 3, 'name': 'French', 'language': 'fre'})
        self.poll()
        self.poll()
        self.enabled = False
        self.poll()
        self.poll()
        self.assertTrue((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_stream_reset_does_not_create_manual_memory(self):
        self.start()
        self.streams = []
        self.current = {}
        self.enabled = False
        self.poll()
        self.streams = [{'index': 0, 'name': 'Signs', 'language': 'eng'}]
        self.current = dict(self.streams[0])
        self.enabled = True
        self.poll()
        self.poll()
        self.assertFalse((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_duplicate_label_manual_sibling_is_recorded(self):
        self.streams[1] = dict(self.streams[0], index=1)
        self.start()
        self.current = dict(self.streams[1])
        self.poll()
        self.poll()
        self.assertTrue((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_delayed_confirmation_still_records_manual_off(self):
        rpc = self.rpc
        pending = {'stream': None, 'reads': 0}

        def delayed_rpc(method, params=None):
            if method == 'Player.SetSubtitle':
                value = params['subtitle']
                self.writes.append(value)
                if type(value) is int:
                    pending['stream'] = dict(self.streams[value])
                self.player.onAVChange()
                return {'result': 'OK'}
            if method == 'Player.GetProperties' and pending['stream'] is not None:
                pending['reads'] += 1
                if pending['reads'] >= 2:
                    self.current = pending['stream']
                    self.enabled = True
            return rpc(method, params)

        self.subtitles._jsonrpc = delayed_rpc
        self.start()
        self.subtitles._jsonrpc = rpc
        self.enabled = False
        self.poll()
        self.poll()
        self.assertTrue((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_missing_snapshot_does_not_record_off(self):
        self.start()
        self.enabled = False
        original = self.subtitles._get_playback_snapshot
        self.subtitles._get_playback_snapshot = lambda: None
        self.poll()
        self.poll()
        self.subtitles._get_playback_snapshot = original
        self.enabled = True
        self.poll()
        self.assertFalse((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_start_snapshot_error_does_not_prevent_selection(self):
        original = self.subtitles._get_playback_snapshot
        reads = [0]

        def snapshot():
            reads[0] += 1
            if reads[0] == 1:
                raise RuntimeError('player properties unavailable')
            return original()

        self.subtitles._get_playback_snapshot = snapshot
        self.start()
        self.assertEqual([0, 'on'], self.writes)

    def test_late_stream_after_missing_initial_snapshot_is_selected(self):
        self.streams = [{'index': 1, 'name': 'English', 'language': 'eng'}]
        snapshot = self.subtitles._get_playback_snapshot
        self.subtitles._get_playback_snapshot = lambda: None
        self.start()
        self.subtitles._get_playback_snapshot = snapshot
        self.streams.append({'index': 2, 'name': 'Signs', 'language': 'eng'})
        self.player.onAVChange()
        self.poll()
        self.poll()
        self.assertEqual([2, 'on'], self.writes)

    def test_manual_off_records_and_skips_next_episode(self):
        self.start()
        self.enabled = False
        self.player.onAVChange()
        self.poll()
        self.poll()
        self.assertEqual('skip|episode1.mkv', HOME.properties['SmartSubtitles.Result'])
        self.player.playing_file = 'episode2.mkv'
        self.current = {}
        self.start()
        self.assertEqual([0, 'on'], self.writes)
        self.assertEqual('skip|episode2.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_manual_other_track_is_authoritative_and_persistent(self):
        self.start()
        self.current = dict(self.streams[1])
        self.poll()
        self.poll()
        self.assertEqual([0, 'on'], self.writes)
        self.assertTrue((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_transient_off_does_not_create_memory(self):
        self.start()
        self.enabled = False
        self.poll(0.1)
        self.enabled = True
        self.poll()
        self.assertFalse((Path(self.temp.name) / 'manual-corrections.json').exists())

    def test_disable_memory_still_respects_manual_choice_for_current_file(self):
        self.settings['remember_manual'] = False
        self.start()
        self.enabled = False
        self.poll()
        self.poll()
        self.assertEqual([0, 'on'], self.writes)
        self.assertFalse((Path(self.temp.name) / 'manual-corrections.json').exists())
        self.player.playing_file = 'episode2.mkv'
        self.current = {}
        self.start()
        self.assertEqual([0, 'on', 0, 'on'], self.writes)

    def test_file_fallback_record_survives_later_show_metadata(self):
        self.player._memory.record(self.corrections.correction_key('episode1.mkv', {}))
        self.start()
        self.assertEqual([], self.writes)
        self.assertEqual('skip|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

    def test_show_correction_also_covers_file_when_metadata_disappears(self):
        self.start()
        self.enabled = False
        self.poll()
        self.poll()
        rpc = self.rpc
        self.subtitles._jsonrpc = lambda method, params=None: (
            {'result': {'item': {}}} if method == 'Player.GetItem' else rpc(method, params))
        self.current = {}
        self.start()
        self.assertEqual([0, 'on'], self.writes)

    def test_memory_disable_ignores_existing_saved_correction(self):
        self.player._memory.record(self.corrections.correction_key('episode1.mkv', {}))
        self.settings['remember_manual'] = False
        self.start()
        self.assertEqual([0, 'on'], self.writes)

    def test_changed_file_without_start_callback_does_not_write(self):
        self.start()
        self.player.playing_file = 'episode2.mkv'
        self.enabled = False
        self.poll()
        self.poll()
        self.assertEqual([0, 'on'], self.writes)
        self.assertFalse((Path(self.temp.name) / 'manual-corrections.json').exists())


if __name__ == '__main__':
    unittest.main()
