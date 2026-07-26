# -*- coding: utf-8 -*-
# GNU General Public License v2.0 (see COPYING or https://www.gnu.org/licenses/gpl-2.0.txt)
from __future__ import absolute_import, division, unicode_literals

import json

import xbmc
import xbmcgui


LANGUAGE_CODES = {
    'en': 'eng',
    'eng': 'eng',
    'english': 'eng',
}


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


def _disable_subtitles(playerid):
    _jsonrpc('Player.SetSubtitle', {'playerid': playerid, 'subtitle': 'off'})


def _get_keywords():
    raw = _get_setting('keywords') or 'signs,songs,sign,forced'
    return [k.strip().lower() for k in raw.split(',') if k.strip()]


def _language_code(language):
    """Return a stable base language code for a Kodi language value."""
    if not isinstance(language, str):
        return ''
    value = language.strip().lower().replace('_', '-')
    if not value:
        return ''
    base_language = value.split('-', 1)[0]
    try:
        converted = xbmc.convertLanguage(base_language, xbmc.ISO_639_2)
    except (AttributeError, TypeError):
        converted = ''
    if converted:
        return converted.lower()
    return LANGUAGE_CODES.get(value, LANGUAGE_CODES.get(base_language, base_language))


def _get_preferred_languages():
    """Return the configured subtitle language codes, defaulting to English."""
    raw = _get_setting('preferred_languages') or 'English'
    languages = set()
    for language in raw.split(','):
        code = _language_code(language)
        if code:
            languages.add(code)
    return languages


def _matches_preferred_language(stream, preferred_languages):
    """Only auto-select tracks Kodi identifies as one of the preferred languages."""
    return _language_code(stream.get('language')) in preferred_languages


def _stream_for_index(streams, index):
    """Return the subtitle stream with the given Kodi index, if present."""
    for stream in streams:
        if stream.get('index') == index:
            return stream
    return None


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

    keywords = _get_keywords()
    preferred_languages = _get_preferred_languages()

    # Preserve Kodi's already active preferred-language subtitle for THIS file.
    # Kodi's `currentsubtitle.index` carries across files (stale cached value
    # reported for tens of ms after onAVStarted), so by itself it is NOT a
    # reliable signal that Kodi resolved the current file. The reliable signal is
    # `subtitleenabled=true` together with a current stream means Kodi has
    # resolved the new file's subtitle state and subtitles are actively
    # rendering. If subtitleenabled=false, Kodi fell through forced_only with
    # no match and disabled subtitles, even if a stale index was reported.
    current_index = current.get('index')
    current_stream = _stream_for_index(streams, current_index)
    if sub_enabled and current_stream is not None:
        if current_stream and not _matches_preferred_language(current_stream, preferred_languages):
            if current_stream.get('isforced'):
                xbmc.log('Smart Subtitles: disabling non-preferred forced subtitle '
                         '(index=%s language=%s)' % (
                             current_index, current_stream.get('language', '')), xbmc.LOGINFO)
                _disable_subtitles(playerid)
            else:
                xbmc.log('Smart Subtitles: subtitle already active and enabled '
                         '(index=%s), not overriding' % current_index, xbmc.LOGINFO)
                return 'skip'
        else:
            xbmc.log('Smart Subtitles: subtitle already active and enabled '
                     '(index=%s), not overriding' % current_index, xbmc.LOGINFO)
            return 'skip'

    xbmc.log('Smart Subtitles: scanning %d streams (sub_enabled=%s, current_index=%s) '
             'for keywords %s in languages %s' % (
                 len(streams), sub_enabled, current_index, keywords,
                 sorted(preferred_languages)), xbmc.LOGINFO)
    for stream in streams:
        name = stream.get('name', '')
        index = stream.get('index')
        language = stream.get('language', '')
        xbmc.log('Smart Subtitles: stream[%s] language=%s name=%s' % (
            index, language, name), xbmc.LOGDEBUG)
        if (_matches_keywords(name, keywords)
                and _matches_preferred_language(stream, preferred_languages)):
            xbmc.log('Smart Subtitles: SELECTING index=%d name=%s' % (index, name), xbmc.LOGINFO)
            _set_subtitle(playerid, index)
            _notify_selected(name)
            return 'selected'

    xbmc.log('Smart Subtitles: no matching track found in preferred languages', xbmc.LOGINFO)
    return 'no_match'
