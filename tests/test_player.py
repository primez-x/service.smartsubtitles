import importlib.util
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).parents[1] / 'resources' / 'lib' / 'player.py'


class FakePlayer(object):
    def __init__(self):
        self.playing_file = 'episode1.mkv'

    def getPlayingFile(self):
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


def load_player_module(select):
    xbmc = types.SimpleNamespace(
        LOGINFO=1,
        Player=FakePlayer,
        Monitor=FakeMonitor,
        log=lambda _message, _level: None,
    )
    xbmc.LOGERROR = 4
    subtitles = types.SimpleNamespace(select_signs_songs_track=select)
    xbmcgui = types.SimpleNamespace(Window=lambda _window_id: HOME)
    with mock.patch.dict(sys.modules, {'xbmc': xbmc, 'xbmcgui': xbmcgui,
                                       'subtitles': subtitles}):
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

        def select(should_stop=None):
            self.calls.append(should_stop)
            result = results.pop(0) if len(results) > 1 else results[0]
            if isinstance(result, Exception):
                raise result
            return result

        module = load_player_module(select)
        module.RETRY_INTERVAL = interval
        return module.SmartSubtitlesPlayer()

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

    def test_selection_error_ends_the_attempt(self):
        player = self._player([ValueError('bad reply'), 'selected'])
        player._select_with_retry(threading.Event(), 'episode1.mkv')
        self.assertEqual(1, len(self.calls))
        self.assertEqual('error|episode1.mkv', HOME.properties['SmartSubtitles.Result'])

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


if __name__ == '__main__':
    unittest.main()
