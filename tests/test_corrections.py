import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock


SOURCE = Path(__file__).parents[1] / 'resources' / 'lib' / 'corrections.py'


def load_corrections_module():
    xbmc = types.SimpleNamespace(LOGWARNING=2, log=lambda *_args: None)
    with mock.patch.dict(sys.modules, {'xbmc': xbmc}):
        spec = importlib.util.spec_from_file_location('corrections_under_test', SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


class CorrectionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.module = load_corrections_module()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'profile' / 'manual-corrections.json'
        self.memory = self.module.CorrectionMemory(self.path)

    def test_records_show_key_and_reuses_for_later_episodes(self):
        first = self.module.correction_key('/tv/show/one.mkv',
            {'type': 'episode', 'tvshowid': 17, 'showtitle': 'Show', 'file': '/tv/show/one.mkv'})
        second = self.module.correction_key('/tv/show/two.mkv',
            {'type': 'episode', 'tvshowid': 17, 'showtitle': 'SHOW', 'file': '/tv/show/two.mkv'})
        self.memory.record(first)
        self.assertTrue(self.module.CorrectionMemory(self.path).contains(second))
        data = json.loads(self.path.read_text())
        self.assertEqual(1, data['version'])
        self.assertNotIn('/tv/', self.path.read_text())

    def test_reused_library_id_does_not_skip_a_different_show(self):
        first = self.module.correction_key('one.mkv',
            {'type': 'episode', 'tvshowid': 17, 'showtitle': 'Show One', 'file': 'one.mkv'})
        second = self.module.correction_key('two.mkv',
            {'type': 'episode', 'tvshowid': 17, 'showtitle': 'Show Two', 'file': 'two.mkv'})
        self.memory.record(first)
        self.assertFalse(self.memory.contains(second))

    def test_file_fallback_does_not_guess_show_from_title(self):
        first = self.module.correction_key('plugin://video/?token=private',
                                           {'type': 'episode', 'showtitle': 'Show'})
        self.memory.record(first)
        self.assertTrue(self.memory.contains(first))
        self.assertFalse(self.memory.contains(self.module.correction_key(
            'episode2.mkv', {'type': 'episode', 'showtitle': 'Show'})))
        self.assertNotIn('private', self.path.read_text())

    def test_stale_metadata_and_movies_use_file_key(self):
        for item in ({'type': 'episode', 'tvshowid': 17, 'file': 'other.mkv'},
                     {'type': 'episode', 'tvshowid': 17, 'file': 'one.mkv'},
                     {'type': 'movie', 'tvshowid': 17, 'file': 'one.mkv'},
                     {'type': 'episode', 'tvshowid': True, 'file': 'one.mkv'}):
            self.assertEqual(self.module.correction_key('one.mkv', {}),
                             self.module.correction_key('one.mkv', item))

    def test_no_identity_creates_no_record_or_file(self):
        self.assertIsNone(self.module.correction_key('', {}))
        self.memory.record(None)
        self.assertFalse(self.memory.contains(None))
        self.assertFalse(self.path.exists())

    def test_corrupt_and_invalid_json_are_safe_and_can_be_replaced(self):
        self.path.parent.mkdir()
        key = self.module.correction_key('one.mkv', {})
        for data in ('{broken', '[]', '{"version": 99, "entries": {}}',
                     '{"version": 1, "entries": {"../bad": 1}}'):
            self.path.write_text(data)
            memory = self.module.CorrectionMemory(self.path)
            self.assertFalse(memory.contains(key), data)
            memory.record(key)
            self.assertTrue(self.module.CorrectionMemory(self.path).contains(key))

    def test_oldest_entries_are_evicted(self):
        self.module.MAX_ENTRIES = 2
        keys = [self.module.correction_key(name, {})
                for name in ('one.mkv', 'two.mkv', 'three.mkv')]
        with mock.patch.object(self.module.time, 'time', side_effect=[10, 20, 30]):
            for key in keys:
                self.memory.record(key)
        fresh = self.module.CorrectionMemory(self.path)
        self.assertFalse(fresh.contains(keys[0]))
        self.assertTrue(fresh.contains(keys[1]))
        self.assertTrue(fresh.contains(keys[2]))

    def test_oversized_json_is_ignored(self):
        self.path.parent.mkdir()
        self.path.write_text(' ' * 65537)
        self.assertFalse(self.memory.contains(self.module.correction_key('one', {})))

    def test_invalid_timestamps_are_safe(self):
        self.path.parent.mkdir()
        key = self.module.correction_key('one', {})
        for stamp in (True, -1, float('nan'), float('inf'), 10 ** 1000):
            self.path.write_text(json.dumps({'version': 1, 'entries': {key: stamp}}))
            memory = self.module.CorrectionMemory(self.path)
            self.assertFalse(memory.contains(key))

    def test_failed_temp_cleanup_does_not_raise(self):
        key = self.module.correction_key('one', {})
        with mock.patch.object(self.module.os, 'replace', side_effect=PermissionError('locked')), \
                mock.patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
            self.memory.record(key)
        self.assertTrue(self.memory.contains(key))

    def test_write_failure_keeps_session_memory(self):
        self.path.parent.mkdir()
        self.path.mkdir()
        key = self.module.correction_key('one', {})
        self.memory.record(key)
        self.assertTrue(self.memory.contains(key))
