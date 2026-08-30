# In-memory TTML for Folia lyrics endpoints.
# getLyricsBySongId returns one unsynced line whose value is the full TTML so
# Folia 5f1c966 hydrateNavidromeLyricPayload falls back to getLyrics / detectTimedLyricFormat.
# This is NOT OpenSubsonic structuredLyrics semantics. songLyrics is not declared.

from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from subsonic.deps import SubsonicDeps

_CACHE_LOCK = Lock()
_CACHE = OrderedDict()
_CACHE_LIMIT = 64


def _mtime(path: Optional[Path]) -> float:
    if path is None:
        return 0.0
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _resolve_lyric_file(deps: SubsonicDeps, value: str) -> Optional[Path]:
    text = (value or '').strip()
    if not text or text == '!':
        return None
    lower = text.lower()
    if lower.startswith('data:'):
        return None
    relative = deps.normalize_audio_ref(text)
    candidate = relative or text
    try:
        path = deps.resolve_songs_path(candidate)
    except Exception:
        return None
    if path.is_file():
        return path
    return None


def _cache_get(key: Tuple) -> Optional[str]:
    with _CACHE_LOCK:
        if key not in _CACHE:
            return None
        _CACHE.move_to_end(key)
        return _CACHE[key]


def _cache_put(key: Tuple, value: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)


def reset_lyrics_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def ttml_for_song(deps: SubsonicDeps, song: Dict[str, Any]) -> Optional[str]:
    main_path = _resolve_lyric_file(deps, str(song.get('lyricsPath') or ''))
    trans_path = _resolve_lyric_file(deps, str(song.get('translationPath') or ''))
    if main_path is None:
        return None
    key = (str(main_path), str(trans_path) if trans_path else '', _mtime(main_path), _mtime(trans_path))
    cached = _cache_get(key)
    if cached is not None:
        return cached

    suffix = main_path.suffix.lower()
    hint = str(trans_path) if trans_path else None
    xml_text = None
    if suffix == '.ttml':
        try:
            xml_text = main_path.read_text(encoding='utf-8')
        except OSError:
            return None
    elif suffix == '.lys':
        ok, payload = deps.lys_to_ttml_text(str(main_path), hint)
        if ok:
            xml_text = payload
    elif suffix == '.lrc':
        ok, payload = deps.lrc_to_ttml_text(str(main_path), hint)
        if ok:
            xml_text = payload
    if not xml_text:
        return None
    _cache_put(key, xml_text)
    return xml_text


def lyrics_payload(song: Dict[str, Any], xml_text: Optional[str]) -> Dict[str, Any]:
    return {
        'lyrics': {
            'artist': song.get('artist') or '',
            'title': song.get('title') or '',
            'value': xml_text or '',
        }
    }


def structured_lyrics_payload(song: Dict[str, Any], xml_text: Optional[str]) -> Dict[str, Any]:
    # Folia fallback contract (5f1c966 appNavidromeLyrics.ts):
    # structuredLyrics without cueLine / enhanced-lrc causes getLyrics + detectTimedLyricFormat(TTML).
    lines = []
    if xml_text:
        lines = [{'value': xml_text}]
    item = {
        'displayArtist': song.get('artist') or '',
        'displayTitle': song.get('title') or '',
        'lang': 'und',
        'offset': 0,
        'synced': False,
        'line': lines,
    }
    return {
        'lyricsList': {
            'structuredLyrics': [item] if xml_text else [],
        }
    }


def lyrics_wrote_disk(before_names, after_names) -> bool:
    return sorted(before_names) != sorted(after_names)
