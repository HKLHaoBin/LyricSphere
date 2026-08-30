# Anchor merge for Folia Phase-2/3: like, listenStats, playlists, revision/tombstone.
# Disk I/O stays in deps.mutate_anchor / backup_client_state.

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

LIKE_PLAYLIST_ID = 'like'
LIKE_DEFAULT_NAME = '喜欢'
HISTORY_CAP = 50
LISTENS_CAP = 50
COMPLETIONS_CAP = 50
MAX_SAFE_INTEGER = 9007199254740991


class IncomingValidationError(ValueError):
    """Client backup body failed A2 validation (map to HTTP 400)."""


class RevisionExhaustedError(ValueError):
    """disk.revision == MAX_SAFE_INTEGER (map to HTTP 409 / Folia code 0)."""


def is_safe_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SAFE_INTEGER


def get_revision(data: Optional[Dict[str, Any]]) -> int:
    if not isinstance(data, dict):
        return 0
    raw = data.get('revision')
    if raw is None:
        return 0
    if not is_safe_int(raw):
        raise ValueError('invalid revision')
    return int(raw)


def dedupe_tracks(tracks: Any) -> List[str]:
    if not isinstance(tracks, list):
        return []
    seen = set()
    out: List[str] = []
    for item in tracks:
        name = str(item) if item is not None else ''
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def normalize_tombstone_map(raw: Any, *, missing_ok: bool = True) -> Dict[str, int]:
    if raw is None:
        if missing_ok:
            return {}
        raise ValueError('tombstone missing')
    if not isinstance(raw, dict):
        raise ValueError('tombstone is not a dict')
    out: Dict[str, int] = {}
    for key, value in raw.items():
        name = str(key) if key is not None else ''
        if not name:
            continue
        if not is_safe_int(value):
            raise ValueError('invalid tombstone value')
        out[name] = int(value)
    return {k: out[k] for k in sorted(out.keys())}


def stamp_zero_tombstones(data: Dict[str, Any], next_revision: int) -> None:
    deleted = data.get('deletedPlaylistIds')
    if isinstance(deleted, dict):
        for key, value in list(deleted.items()):
            if value == 0:
                deleted[key] = next_revision
        data['deletedPlaylistIds'] = normalize_tombstone_map(deleted)
    playlists = data.get('playlists')
    if isinstance(playlists, list):
        for item in playlists:
            if not isinstance(item, dict):
                continue
            removed = item.get('removedTracks')
            if isinstance(removed, dict):
                for key, value in list(removed.items()):
                    if value == 0:
                        removed[key] = next_revision
                item['removedTracks'] = normalize_tombstone_map(removed)


def ensure_revision_room(data: Dict[str, Any]) -> int:
    rev = get_revision(data)
    if rev >= MAX_SAFE_INTEGER:
        raise RevisionExhaustedError('revision exhausted')
    return rev


def finalize_changed_data(data: Dict[str, Any], disk_revision: int) -> None:
    if disk_revision >= MAX_SAFE_INTEGER:
        raise RevisionExhaustedError('revision exhausted')
    next_revision = disk_revision + 1
    stamp_zero_tombstones(data, next_revision)
    data['revision'] = next_revision
    if 'deletedPlaylistIds' in data:
        data['deletedPlaylistIds'] = normalize_tombstone_map(data.get('deletedPlaylistIds'))
    playlists = data.get('playlists')
    if isinstance(playlists, list):
        for item in playlists:
            if isinstance(item, dict) and 'removedTracks' in item:
                item['removedTracks'] = normalize_tombstone_map(item.get('removedTracks'))


def _like_entries(playlists: List[Any]) -> List[Dict[str, Any]]:
    rows = []
    for item in playlists:
        if isinstance(item, dict) and str(item.get('id') or '') == LIKE_PLAYLIST_ID:
            rows.append(item)
    return rows


def ensure_like_playlist(data: Dict[str, Any]) -> bool:
    changed = False
    if 'playlists' not in data:
        data['playlists'] = []
        changed = True
    playlists = data.get('playlists')
    if not isinstance(playlists, list):
        raise ValueError('playlists is not a list')
    likes = _like_entries(playlists)
    if len(likes) > 1:
        raise ValueError('multiple like playlists')
    if len(likes) == 0:
        playlists.append({
            'id': LIKE_PLAYLIST_ID,
            'name': LIKE_DEFAULT_NAME,
            'type': 'manual',
            'artistName': '',
            'tracks': [],
            'removedTracks': {},
        })
        return True
    like = likes[0]
    if 'tracks' not in like:
        like['tracks'] = []
        changed = True
    elif not isinstance(like.get('tracks'), list):
        raise ValueError('like.tracks is not a list')
    else:
        deduped = dedupe_tracks(like['tracks'])
        if deduped != [str(x) for x in like['tracks'] if x]:
            # only rewrite if duplicates or non-str noise
            if deduped != like['tracks']:
                like['tracks'] = deduped
                changed = True
    if 'removedTracks' in like and like['removedTracks'] is not None:
        if not isinstance(like['removedTracks'], dict):
            raise ValueError('like.removedTracks is not a dict')
    return changed


def _clear_removed_track(playlist: Dict[str, Any], filename: str) -> bool:
    removed = playlist.get('removedTracks')
    if not isinstance(removed, dict) or filename not in removed:
        return False
    del removed[filename]
    playlist['removedTracks'] = normalize_tombstone_map(removed)
    return True


def star_track(data: Dict[str, Any], filename: str) -> bool:
    ensure_revision_room(data)
    changed = ensure_like_playlist(data)
    like = _like_entries(data['playlists'])[0]
    tracks = dedupe_tracks(like.get('tracks'))
    like['tracks'] = tracks
    cleared = _clear_removed_track(like, filename)
    if filename in tracks:
        return changed or cleared
    tracks.append(filename)
    return True


def history_touch(data: Dict[str, Any], filename: str) -> bool:
    if 'history' not in data:
        data['history'] = []
    history = data.get('history')
    if not isinstance(history, list):
        raise ValueError('history is not a list')
    if history and history[0] == filename and filename not in history[1:]:
        return False
    next_history = [filename] + [item for item in history if item != filename]
    data['history'] = next_history[:HISTORY_CAP]
    return True


def legacy_listen_count(entry: Optional[Dict[str, Any]]) -> int:
    if not isinstance(entry, dict):
        return 0
    count = entry.get('listenCount')
    if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        return count
    listens = entry.get('listens') if isinstance(entry.get('listens'), list) else []
    completions = entry.get('completions') if isinstance(entry.get('completions'), list) else []
    return len(listens) or len(completions)


def normalize_listen_entry(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(entry, dict):
        return {'completions': [], 'listens': [], 'listenCount': 0}
    completions = list(entry['completions']) if isinstance(entry.get('completions'), list) else []
    listens = list(entry['listens']) if isinstance(entry.get('listens'), list) else []
    return {
        'completions': completions[-COMPLETIONS_CAP:],
        'listens': listens[-LISTENS_CAP:],
        'listenCount': legacy_listen_count({
            **entry,
            'completions': completions,
            'listens': listens,
        }),
    }


def apply_scrobble(
    data: Dict[str, Any],
    filename: str,
    *,
    time_ms: int,
    submission: bool,
) -> bool:
    if submission:
        return False
    ensure_revision_room(data)
    stats = data.get('listenStats')
    if stats is None:
        data['listenStats'] = {}
        stats = data['listenStats']
    if not isinstance(stats, dict):
        raise ValueError('listenStats is not a dict')
    entry = normalize_listen_entry(stats.get(filename))
    listens = list(entry['listens'])
    if time_ms in listens:
        stats[filename] = entry
        return False
    listens.append(time_ms)
    entry['listens'] = listens[-LISTENS_CAP:]
    entry['listenCount'] = int(entry.get('listenCount') or 0) + 1
    stats[filename] = entry
    history_touch(data, filename)
    return True


def _parse_listen_ms(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value < 0 or value != int(value):
            return None
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = int(value.strip())
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def last_listen_ms_by_filename(data: Optional[Dict[str, Any]]) -> Dict[str, int]:
    if not isinstance(data, dict):
        return {}
    stats = data.get('listenStats')
    if not isinstance(stats, dict):
        return {}
    out: Dict[str, int] = {}
    for filename, entry in stats.items():
        if not filename or not isinstance(entry, dict):
            continue
        listens = entry.get('listens')
        if not isinstance(listens, list):
            continue
        best = None
        for item in listens:
            parsed = _parse_listen_ms(item)
            if parsed is None:
                continue
            if best is None or parsed > best:
                best = parsed
        if best is not None:
            out[str(filename)] = best
    return out


def _playlist_by_id(data: Dict[str, Any], playlist_id: str) -> Optional[Dict[str, Any]]:
    playlists = data.get('playlists')
    if not isinstance(playlists, list):
        raise ValueError('playlists is not a list')
    found = None
    for item in playlists:
        if isinstance(item, dict) and str(item.get('id') or '') == playlist_id:
            if found is not None:
                raise ValueError('duplicate playlist id')
            found = item
    return found


def allocate_playlist_id(data: Dict[str, Any], *, now_ms: Optional[int] = None) -> str:
    ms = int(now_ms if now_ms is not None else time.time() * 1000)
    playlists = data.get('playlists') if isinstance(data.get('playlists'), list) else []
    existing_ids = {
        str(item.get('id'))
        for item in playlists
        if isinstance(item, dict) and item.get('id')
    }
    if data.get('deletedPlaylistIds') is not None and not isinstance(data.get('deletedPlaylistIds'), dict):
        raise ValueError('deletedPlaylistIds is not a dict')
    deleted = normalize_tombstone_map(data.get('deletedPlaylistIds'))
    while True:
        candidate = f'pl-{ms}'
        if candidate not in existing_ids and candidate not in deleted:
            return candidate
        ms += 1


def create_manual_playlist(
    data: Dict[str, Any],
    name: str,
    filenames: List[str],
    *,
    now_ms: Optional[int] = None,
) -> Tuple[bool, str]:
    ensure_revision_room(data)
    if 'playlists' not in data:
        data['playlists'] = []
    if not isinstance(data.get('playlists'), list):
        raise ValueError('playlists is not a list')
    tracks = dedupe_tracks(filenames)
    playlist_id = allocate_playlist_id(data, now_ms=now_ms)
    # Creating a playlist with an id that was deleted: allocate_playlist_id avoids that.
    data['playlists'].append({
        'id': playlist_id,
        'name': name,
        'type': 'manual',
        'artistName': '',
        'tracks': tracks,
        'removedTracks': {},
    })
    return True, playlist_id


def update_manual_playlist(
    data: Dict[str, Any],
    playlist_id: str,
    *,
    add_filenames: Optional[List[str]] = None,
    remove_indexes: Optional[List[int]] = None,
    rename_to: Optional[str] = None,
) -> str:
    """Returns 'ok' | 'noop' | 'rename_denied' | 'not_found' | 'bad_index'."""
    ensure_revision_room(data)
    deleted = normalize_tombstone_map(data.get('deletedPlaylistIds'))
    if playlist_id in deleted:
        return 'not_found'
    playlist = _playlist_by_id(data, playlist_id)
    if playlist is None:
        return 'not_found'
    if rename_to is not None and rename_to != str(playlist.get('name') or ''):
        return 'rename_denied'
    if 'tracks' in playlist and not isinstance(playlist.get('tracks'), list):
        raise ValueError('tracks is not a list')
    if 'removedTracks' in playlist and playlist['removedTracks'] is not None and not isinstance(playlist.get('removedTracks'), dict):
        raise ValueError('removedTracks is not a dict')

    playlist['tracks'] = dedupe_tracks(playlist.get('tracks'))
    playlist['removedTracks'] = dict(normalize_tombstone_map(playlist.get('removedTracks')))
    changed = False

    if remove_indexes:
        unique_indexes = sorted({int(i) for i in remove_indexes}, reverse=True)
        for index in unique_indexes:
            if index < 0 or index >= len(playlist['tracks']):
                return 'bad_index'
        for index in unique_indexes:
            filename = playlist['tracks'].pop(index)
            playlist['removedTracks'][filename] = 0
            changed = True
        playlist['removedTracks'] = normalize_tombstone_map(playlist['removedTracks'])

    for filename in dedupe_tracks(add_filenames or []):
        if _clear_removed_track(playlist, filename):
            changed = True
        if filename not in playlist['tracks']:
            playlist['tracks'].append(filename)
            changed = True

    playlist['tracks'] = dedupe_tracks(playlist['tracks'])
    return 'ok' if changed else 'noop'


def delete_manual_playlist(data: Dict[str, Any], playlist_id: str) -> str:
    """Returns 'ok' | 'noop' | 'forbidden' | 'not_found'."""
    if playlist_id == LIKE_PLAYLIST_ID:
        return 'forbidden'
    ensure_revision_room(data)
    if 'playlists' not in data:
        data['playlists'] = []
    if not isinstance(data.get('playlists'), list):
        raise ValueError('playlists is not a list')
    deleted = normalize_tombstone_map(data.get('deletedPlaylistIds'))
    data['deletedPlaylistIds'] = dict(deleted)
    playlists = data['playlists']
    remaining = []
    found = False
    for item in playlists:
        if isinstance(item, dict) and str(item.get('id') or '') == playlist_id:
            found = True
            continue
        remaining.append(item)
    if not found and playlist_id in deleted:
        return 'noop'
    if not found:
        return 'not_found'
    data['playlists'] = remaining
    data['deletedPlaylistIds'][playlist_id] = 0
    data['deletedPlaylistIds'] = normalize_tombstone_map(data['deletedPlaylistIds'])
    return 'ok'


def unstar_track(data: Dict[str, Any], filename: str) -> bool:
    ensure_revision_room(data)
    ensure_like_playlist(data)
    like = _like_entries(data['playlists'])[0]
    tracks = dedupe_tracks(like.get('tracks'))
    like['tracks'] = tracks
    if filename not in tracks:
        return False
    like['tracks'] = [item for item in tracks if item != filename]
    removed = normalize_tombstone_map(like.get('removedTracks'))
    removed[filename] = 0
    like['removedTracks'] = normalize_tombstone_map(removed)
    return True


def validate_incoming_backup_data(
    incoming: Dict[str, Any],
    *,
    disk_revision: Optional[int],
    file_exists: bool,
) -> Dict[str, Any]:
    """Normalize+validate client backup data. Raises IncomingValidationError."""
    if not isinstance(incoming, dict):
        raise IncomingValidationError('data must be an object')

    if 'revision' not in incoming or incoming.get('revision') is None:
        rev = 0
    else:
        if not is_safe_int(incoming.get('revision')):
            raise IncomingValidationError('invalid revision')
        rev = int(incoming['revision'])

    try:
        deleted = normalize_tombstone_map(incoming.get('deletedPlaylistIds'))
    except ValueError as exc:
        raise IncomingValidationError(str(exc)) from exc

    playlists_in = incoming.get('playlists')
    if playlists_in is None:
        playlists_in = []
    if not isinstance(playlists_in, list):
        raise IncomingValidationError('playlists must be a list')

    normalized_playlists: List[Dict[str, Any]] = []
    for item in playlists_in:
        if not isinstance(item, dict) or not item.get('id'):
            continue
        try:
            removed = normalize_tombstone_map(item.get('removedTracks'))
        except ValueError as exc:
            raise IncomingValidationError(str(exc)) from exc
        tracks = dedupe_tracks(item.get('tracks'))
        overlap = set(tracks) & set(removed.keys())
        if overlap:
            raise IncomingValidationError('track also in removedTracks')
        row = {
            'id': str(item.get('id')),
            'name': str(item.get('name') or item.get('title') or item.get('id')),
            'type': str(item.get('type') or 'manual'),
            'artistName': str(item.get('artistName') or ''),
            'tracks': tracks,
            'removedTracks': removed,
        }
        normalized_playlists.append(row)

    if not file_exists:
        if rev != 0:
            raise IncomingValidationError('first create requires revision 0')
        for value in deleted.values():
            if value != 0:
                raise IncomingValidationError('first create forbids positive tombstones')
        for row in normalized_playlists:
            for value in row['removedTracks'].values():
                if value != 0:
                    raise IncomingValidationError('first create forbids positive tombstones')
    else:
        assert disk_revision is not None
        if rev > disk_revision:
            raise IncomingValidationError('incoming revision ahead of disk')
        if rev == 0:
            for value in deleted.values():
                if value != 0:
                    raise IncomingValidationError('revision 0 forbids positive tombstones')
            for row in normalized_playlists:
                for value in row['removedTracks'].values():
                    if value != 0:
                        raise IncomingValidationError('revision 0 forbids positive tombstones')
        for value in deleted.values():
            if value > rev:
                raise IncomingValidationError('tombstone ahead of incoming revision')
        for row in normalized_playlists:
            for value in row['removedTracks'].values():
                if value > rev:
                    raise IncomingValidationError('tombstone ahead of incoming revision')

    out = dict(incoming)
    out['revision'] = rev
    out['deletedPlaylistIds'] = deleted
    out['playlists'] = normalized_playlists
    if 'artistShortcuts' in incoming and incoming['artistShortcuts'] is not None and not isinstance(incoming['artistShortcuts'], list):
        raise IncomingValidationError('artistShortcuts must be a list')
    if 'history' in incoming and incoming['history'] is not None and not isinstance(incoming['history'], list):
        raise IncomingValidationError('history must be a list')
    if 'listenStats' in incoming and incoming['listenStats'] is not None and not isinstance(incoming['listenStats'], dict):
        raise IncomingValidationError('listenStats must be a dict')
    return out


def _merge_removed_maps(
    disk_removed: Dict[str, int],
    incoming_removed: Dict[str, int],
    *,
    disk_tracks: List[str],
    disk_revision: int,
    incoming_tracks: List[str],
) -> Dict[str, int]:
    merged = dict(disk_removed)
    for key, value in incoming_removed.items():
        if value == 0:
            merged[key] = 0
            continue
        if (
            key in disk_tracks
            and key not in disk_removed
            and value < disk_revision
        ):
            continue
        prev = merged.get(key)
        if prev is None or value > prev:
            merged[key] = value
    for filename in incoming_tracks:
        if filename in merged:
            del merged[filename]
    return normalize_tombstone_map(merged)


def apply_lyricsphere_snapshot(
    disk: Dict[str, Any],
    incoming: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """Merge LyricSphere upload into disk data. Returns (data, changed)."""
    disk_rev = get_revision(disk)
    ensure_revision_room(disk)

    disk_deleted = normalize_tombstone_map(disk.get('deletedPlaylistIds'))
    inc_deleted = normalize_tombstone_map(incoming.get('deletedPlaylistIds'))
    # like cannot be deleted
    inc_deleted.pop(LIKE_PLAYLIST_ID, None)
    disk_deleted.pop(LIKE_PLAYLIST_ID, None)

    merged_deleted: Dict[str, int] = dict(disk_deleted)
    for key, value in inc_deleted.items():
        if value == 0:
            merged_deleted[key] = 0
            continue
        prev = merged_deleted.get(key)
        if prev is None or value > prev:
            merged_deleted[key] = value
    merged_deleted = normalize_tombstone_map(merged_deleted)

    disk_playlists = {
        str(item.get('id')): item
        for item in (disk.get('playlists') or [])
        if isinstance(item, dict) and item.get('id')
    }
    inc_playlists = {
        str(item.get('id')): item
        for item in (incoming.get('playlists') or [])
        if isinstance(item, dict) and item.get('id')
    }
    all_ids = list(dict.fromkeys(list(inc_playlists.keys()) + list(disk_playlists.keys())))

    result_playlists: List[Dict[str, Any]] = []
    for playlist_id in all_ids:
        if playlist_id in merged_deleted:
            continue
        disk_pl = disk_playlists.get(playlist_id)
        inc_pl = inc_playlists.get(playlist_id)
        if disk_pl is None and inc_pl is None:
            continue
        if inc_pl is None and disk_pl is not None:
            # keep disk copy
            row = {
                'id': playlist_id,
                'name': str(disk_pl.get('name') or playlist_id),
                'type': str(disk_pl.get('type') or 'manual'),
                'artistName': str(disk_pl.get('artistName') or ''),
                'tracks': dedupe_tracks(disk_pl.get('tracks')),
                'removedTracks': normalize_tombstone_map(disk_pl.get('removedTracks')),
            }
            result_playlists.append(row)
            continue
        assert inc_pl is not None
        disk_tracks = dedupe_tracks((disk_pl or {}).get('tracks'))
        inc_tracks = dedupe_tracks(inc_pl.get('tracks'))
        disk_removed = normalize_tombstone_map((disk_pl or {}).get('removedTracks'))
        inc_removed = normalize_tombstone_map(inc_pl.get('removedTracks'))
        merged_removed = _merge_removed_maps(
            disk_removed,
            inc_removed,
            disk_tracks=disk_tracks,
            disk_revision=disk_rev,
            incoming_tracks=inc_tracks,
        )
        union: List[str] = []
        seen = set()
        for name in inc_tracks + disk_tracks:
            if name in seen:
                continue
            seen.add(name)
            if name in merged_removed:
                continue
            union.append(name)
        result_playlists.append({
            'id': playlist_id,
            'name': str(inc_pl.get('name') or (disk_pl or {}).get('name') or playlist_id),
            'type': str(inc_pl.get('type') or (disk_pl or {}).get('type') or 'manual'),
            'artistName': str(
                inc_pl.get('artistName')
                if inc_pl.get('artistName') is not None
                else ((disk_pl or {}).get('artistName') or '')
            ),
            'tracks': union,
            'removedTracks': merged_removed,
        })

    if isinstance(incoming.get('artistShortcuts'), list):
        artist_shortcuts = incoming['artistShortcuts']
    elif isinstance(disk.get('artistShortcuts'), list):
        artist_shortcuts = disk['artistShortcuts']
    else:
        artist_shortcuts = []

    if isinstance(incoming.get('listenStats'), dict):
        listen_stats = incoming['listenStats']
    elif isinstance(disk.get('listenStats'), dict):
        listen_stats = disk['listenStats']
    else:
        listen_stats = {}

    if isinstance(incoming.get('history'), list):
        history = incoming['history']
    elif isinstance(disk.get('history'), list):
        history = disk['history']
    else:
        history = []

    merged = {
        'revision': disk_rev,
        'deletedPlaylistIds': merged_deleted,
        'playlists': result_playlists,
        'artistShortcuts': artist_shortcuts,
        'listenStats': listen_stats,
        'history': history,
    }

    # Preserve other unknown keys from disk lightly
    for key, value in disk.items():
        if key not in merged:
            merged[key] = value

    changed = _snapshot_signature(merged) != _snapshot_signature({
        **disk,
        'revision': disk_rev,
        'deletedPlaylistIds': normalize_tombstone_map(disk.get('deletedPlaylistIds')),
        'playlists': [
            {
                'id': str(p.get('id')),
                'name': str(p.get('name') or ''),
                'type': str(p.get('type') or 'manual'),
                'artistName': str(p.get('artistName') or ''),
                'tracks': dedupe_tracks(p.get('tracks')),
                'removedTracks': normalize_tombstone_map(p.get('removedTracks')),
            }
            for p in (disk.get('playlists') or [])
            if isinstance(p, dict) and p.get('id')
        ],
        'artistShortcuts': disk.get('artistShortcuts') if isinstance(disk.get('artistShortcuts'), list) else [],
        'listenStats': disk.get('listenStats') if isinstance(disk.get('listenStats'), dict) else {},
        'history': disk.get('history') if isinstance(disk.get('history'), list) else [],
    })
    return merged, changed


def _snapshot_signature(data: Dict[str, Any]) -> str:
    import json
    payload = {
        'revision': get_revision(data),
        'deletedPlaylistIds': normalize_tombstone_map(data.get('deletedPlaylistIds')),
        'playlists': [
            {
                'id': str(p.get('id')),
                'name': str(p.get('name') or ''),
                'type': str(p.get('type') or 'manual'),
                'artistName': str(p.get('artistName') or ''),
                'tracks': dedupe_tracks(p.get('tracks')),
                'removedTracks': normalize_tombstone_map(p.get('removedTracks')),
            }
            for p in (data.get('playlists') or [])
            if isinstance(p, dict) and p.get('id')
        ],
        'artistShortcuts': data.get('artistShortcuts') if isinstance(data.get('artistShortcuts'), list) else [],
        'listenStats': data.get('listenStats') if isinstance(data.get('listenStats'), dict) else {},
        'history': data.get('history') if isinstance(data.get('history'), list) else [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
