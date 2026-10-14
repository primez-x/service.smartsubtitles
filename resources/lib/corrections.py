"""Bounded profile memory for manual subtitle corrections."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

import xbmc


MAX_ENTRIES = 200
MAX_BYTES = 65536
_KEY_RE = re.compile(r'(?:show|file):[0-9a-f]{64}\Z')


def correction_key(playing_file, item):
    if not isinstance(playing_file, str) or not playing_file:
        return None
    show_id = item.get('tvshowid')
    show_title = item.get('showtitle')
    if (item.get('type') == 'episode' and type(show_id) is int
            and show_id > 0 and item.get('file') == playing_file
            and isinstance(show_title, str) and show_title.strip()):
        kind = 'show'
        identity = 'kodi-tvshow:%d:%s' % (show_id, ' '.join(show_title.split()).casefold())
    else:
        kind, identity = 'file', playing_file
    return '%s:%s' % (kind, hashlib.sha256(identity.encode('utf-8')).hexdigest())


class CorrectionMemory:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.entries = None
        self._warned = False

    def _warn(self, error):
        if not self._warned:
            xbmc.log('Smart Subtitles: manual correction memory unavailable: %s'
                     % error, xbmc.LOGWARNING)
            self._warned = True

    def _load(self):
        if self.entries is not None:
            return
        self.entries = {}
        try:
            if self.path is None:
                import xbmcaddon
                import xbmcvfs
                profile = xbmcaddon.Addon('service.smartsubtitles').getAddonInfo('profile')
                translated = Path(xbmcvfs.translatePath(profile))
                if not profile or not translated.is_absolute():
                    raise ValueError('profile path is unavailable')
                self.path = translated / 'manual-corrections.json'
            if not self.path.exists():
                return
            with self.path.open('rb') as source:
                contents = source.read(MAX_BYTES + 1)
            if len(contents) > MAX_BYTES:
                raise ValueError('profile exceeds size limit')
            data = json.loads(contents.decode('utf-8'))
            if (not isinstance(data, dict) or type(data.get('version')) is not int
                    or data['version'] != 1):
                raise ValueError('invalid profile version')
            entries = data.get('entries')
            if not isinstance(entries, dict):
                raise ValueError('invalid profile entries')
            for key, stamp in entries.items():
                if (not _KEY_RE.fullmatch(key) or type(stamp) not in (float, int)
                        or not math.isfinite(stamp) or stamp < 0):
                    raise ValueError('invalid correction record')
            self.entries = dict(sorted(entries.items(), key=lambda pair: (pair[1], pair[0]))[-MAX_ENTRIES:])
        except (OSError, ValueError, TypeError, ImportError, RuntimeError, OverflowError) as error:
            self._warn(error)

    def contains(self, key):
        if key is None:
            return False
        self._load()
        return key in self.entries

    def record(self, *keys):
        valid = {key for key in keys if isinstance(key, str) and _KEY_RE.fullmatch(key)}
        if not valid:
            return
        self._load()
        stamp = time.time()
        self.entries.update({key: stamp for key in valid})
        self.entries = dict(sorted(self.entries.items(), key=lambda pair: (pair[1], pair[0]))[-MAX_ENTRIES:])
        if self.path is None:
            return
        temporary = self.path.with_suffix('.tmp')
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({'version': 1, 'entries': self.entries},
                                             sort_keys=True), encoding='utf-8')
            os.replace(str(temporary), str(self.path))
        except OSError as error:
            self._warn(error)
        finally:
            try:
                if temporary.exists():
                    temporary.unlink()
            except OSError as error:
                self._warn(error)
