# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
from __future__ import absolute_import, division, unicode_literals

import json

import xbmc
import xbmcgui


def _notify_selected(name):
    """Show a toast notification confirming the subtitle selection."""
    header = xbmc.getLocalizedString(287) or 'Subtitles'  # 287 = "Subtitles"
    xbmcgui.Dialog().notification(header, name, xbmcgui.NOTIFICATION_INFO, 3000)


def _get_setting(key):
    import xbmcaddon
    return xbmcaddon.Addon('service.smartsubtitles').getSetting(key)


def _get_setting_bool(key, default=True):
    val = _get_setting(key)
    if val == '':
        return default
    return val.lower() == 'true'


def _jsonrpc(method, params=None):
    """Execute a JSON-RPC command on Kodi and return the result dict."""
    payload = {'jsonrpc': '2.0', 'method': method, 'id': 1}
    if params is not None:
        payload['params'] = params
    resp = xbmc.executeJSONRPC(json.dumps(payload))
    try:
        return json.loads(resp)
    except (ValueError, TypeError):
        return {}


def _get_active_player_id():
    result = _jsonrpc('Player.GetActivePlayers')
    for player in result.get('result', []) or []:
        if player.get('type') == 'video':
            return player.get('playerid')
    return None


def _get_subtitle_streams(playerid):
    result = _jsonrpc('Player.GetProperties', {
        'playerid': playerid,
        'properties': ['subtitles', 'currentsubtitle', 'subtitleenabled'],
    })
    props = result.get('result', {}) or {}
    return (
        props.get('subtitles', []) or [],
        props.get('currentsubtitle', {}) or {},
        bool(props.get('subtitleenabled', False)),
    )


def _set_subtitle(playerid, index):
    _jsonrpc('Player.SetSubtitle', {'playerid': playerid, 'subtitle': index})
    _jsonrpc('Player.SetSubtitle', {'playerid': playerid, 'subtitle': 'on'})


def _get_keywords():
    raw = _get_setting('keywords') or 'signs,songs,sign,forced'
    return [k.strip().lower() for k in raw.split(',') if k.strip()]


def _matches_keywords(name, keywords):
    if not name:
        return False
    name_lower = name.lower()
    return any(kw in name_lower for kw in keywords)


def select_signs_songs_track():
    """Main entry point. Called on playback start.

    Returns:
      'selected'  — found and selected a matching track
      'no_match'  — scanned streams, none matched keywords
      'disabled'  — addon disabled in settings
      'no_player' — no active video player
      'skip'      — user already has a subtitle active
      'retry'     — no streams available yet (caller should retry)
    """
    if not _get_setting_bool('enabled'):
        xbmc.log('Smart Subtitles: disabled, skipping', xbmc.LOGINFO)
        return 'disabled'

    playerid = _get_active_player_id()
    if playerid is None:
        xbmc.log('Smart Subtitles: no active video player', xbmc.LOGINFO)
        return 'no_player'

    streams, current, sub_enabled = _get_subtitle_streams(playerid)
    if not streams:
        # Streams not enumerated yet — tell caller to retry
        return 'retry'

    # Don't override if the user has actively chosen subtitles for THIS file.
    # Kodi's `currentsubtitle.index` carries across files (stale cached value
    # reported for tens of ms after onAVStarted), so by itself it is NOT a
    # reliable signal of a user choice. The reliable signal is
    # `subtitleenabled=true` together with a non-zero index — that means Kodi
    # has resolved the new file's subtitle state and subtitles are actively
    # rendering. If subtitleenabled=false, Kodi fell through forced_only with
    # no match and disabled subtitles, even if a stale index was reported.
    current_index = current.get('index')
    if sub_enabled and current_index and current_index > 0:
        xbmc.log('Smart Subtitles: subtitle already active and enabled (index=%s), not overriding' % current_index, xbmc.LOGINFO)
        return 'skip'

    keywords = _get_keywords()
    xbmc.log('Smart Subtitles: scanning %d streams (sub_enabled=%s, current_index=%s) for keywords %s' % (
        len(streams), sub_enabled, current_index, keywords), xbmc.LOGINFO)
    for stream in streams:
        name = stream.get('name', '')
        index = stream.get('index')
        xbmc.log('Smart Subtitles: stream[%s] name=%s' % (index, name), xbmc.LOGDEBUG)
        if _matches_keywords(name, keywords):
            xbmc.log('Smart Subtitles: SELECTING index=%d name=%s' % (index, name), xbmc.LOGINFO)
            _set_subtitle(playerid, index)
            _notify_selected(name)
            return 'selected'

    xbmc.log('Smart Subtitles: no matching track found', xbmc.LOGINFO)
    return 'no_match'
