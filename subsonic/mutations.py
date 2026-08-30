# Folia mutations: star, scrobble, playlist CRUD, unstar.

from typing import Any, Dict, List, Optional

from fastapi.responses import JSONResponse

from subsonic import anchor_merge
from subsonic import playlists as plist
from subsonic.deps import SubsonicDeps
from subsonic.envelope import (
    CODE_GENERIC,
    CODE_MISSING_PARAM,
    CODE_NOT_FOUND,
    CODE_UNAUTHORIZED,
    fail_json,
    ok_json,
)


def _map_mutate_result(ok: bool, message: str) -> JSONResponse:
    if ok:
        return ok_json()
    if message == 'anchor missing':
        return fail_json(CODE_NOT_FOUND, 'Anchor not found')
    if message == 'revision exhausted':
        return fail_json(CODE_GENERIC, 'Revision exhausted')
    if message == 'invalid anchor':
        return fail_json(CODE_GENERIC, 'Invalid anchor')
    return fail_json(CODE_GENERIC, 'Failed to update anchor')


def _song_filename(catalog: Dict[str, Any], song_id: Optional[str]) -> Optional[str]:
    if not song_id:
        return None
    song = catalog.get('songsById', {}).get(song_id)
    if not song:
        return None
    filename = song.get('filename')
    return str(filename) if filename else None


def _filenames_from_ids(catalog: Dict[str, Any], song_ids: List[str]) -> Optional[List[str]]:
    out: List[str] = []
    for song_id in song_ids:
        filename = _song_filename(catalog, song_id)
        if not filename:
            return None
        out.append(filename)
    return out


def star_song(
    deps: SubsonicDeps,
    catalog: Dict[str, Any],
    anchor_id: str,
    song_id: Optional[str],
) -> JSONResponse:
    if not song_id:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
    filename = _song_filename(catalog, song_id)
    if not filename:
        return fail_json(CODE_NOT_FOUND, 'Song not found')

    def mutator(data: Dict[str, Any]) -> bool:
        return anchor_merge.star_track(data, filename)

    ok, message = deps.mutate_anchor(anchor_id, mutator)
    return _map_mutate_result(ok, message)


def scrobble_song(
    deps: SubsonicDeps,
    catalog: Dict[str, Any],
    anchor_id: str,
    song_id: Optional[str],
    time_ms: int,
    submission: bool,
) -> JSONResponse:
    if not song_id:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
    filename = _song_filename(catalog, song_id)
    if not filename:
        return fail_json(CODE_NOT_FOUND, 'Song not found')

    def mutator(data: Dict[str, Any]) -> bool:
        return anchor_merge.apply_scrobble(
            data,
            filename,
            time_ms=time_ms,
            submission=submission,
        )

    ok, message = deps.mutate_anchor(anchor_id, mutator)
    return _map_mutate_result(ok, message)


def unstar_song(
    deps: SubsonicDeps,
    catalog: Dict[str, Any],
    anchor_id: str,
    song_id: Optional[str],
) -> JSONResponse:
    if not song_id:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
    filename = _song_filename(catalog, song_id)
    if not filename:
        return fail_json(CODE_NOT_FOUND, 'Song not found')

    def mutator(data: Dict[str, Any]) -> bool:
        return anchor_merge.unstar_track(data, filename)

    ok, message = deps.mutate_anchor(anchor_id, mutator)
    return _map_mutate_result(ok, message)


def create_playlist(
    deps: SubsonicDeps,
    catalog: Dict[str, Any],
    anchor_id: str,
    name: Optional[str],
    song_ids: List[str],
    *,
    has_replace_id: bool,
    username: str,
) -> JSONResponse:
    if has_replace_id:
        return fail_json(CODE_NOT_FOUND, 'createPlaylist with playlistId/id is not supported')
    trimmed = (name or '').strip()
    if not trimmed:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: name')
    filenames = _filenames_from_ids(catalog, song_ids)
    if filenames is None:
        return fail_json(CODE_NOT_FOUND, 'Song not found')

    created_id = {'value': None}

    def mutator(data: Dict[str, Any]) -> bool:
        changed, playlist_id = anchor_merge.create_manual_playlist(data, trimmed, filenames)
        created_id['value'] = playlist_id
        return changed

    ok, message = deps.mutate_anchor(anchor_id, mutator)
    if not ok:
        return _map_mutate_result(ok, message)
    row = plist.get_playlist(deps, anchor_id, created_id['value'], catalog, username)
    if not row:
        return fail_json(CODE_GENERIC, 'Playlist created but not readable')
    return ok_json({'playlist': row})


def update_playlist(
    deps: SubsonicDeps,
    catalog: Dict[str, Any],
    anchor_id: str,
    playlist_id: Optional[str],
    *,
    rename_to: Optional[str],
    song_ids_to_add: List[str],
    song_indexes_to_remove: List[str],
) -> JSONResponse:
    if not playlist_id:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: playlistId')
    add_filenames = _filenames_from_ids(catalog, song_ids_to_add)
    if add_filenames is None:
        return fail_json(CODE_NOT_FOUND, 'Song not found')
    try:
        remove_indexes = [int(str(item).strip()) for item in song_indexes_to_remove]
    except (TypeError, ValueError):
        return fail_json(CODE_MISSING_PARAM, 'Invalid parameter: songIndexToRemove')

    result = {'value': 'noop'}

    def mutator(data: Dict[str, Any]) -> bool:
        status = anchor_merge.update_manual_playlist(
            data,
            playlist_id,
            add_filenames=add_filenames,
            remove_indexes=remove_indexes,
            rename_to=rename_to,
        )
        result['value'] = status
        return status == 'ok'

    ok, message = deps.mutate_anchor(anchor_id, mutator)
    if result['value'] == 'rename_denied':
        return fail_json(CODE_UNAUTHORIZED, 'Renaming playlists is not authorized')
    if result['value'] == 'not_found':
        return fail_json(CODE_NOT_FOUND, 'Playlist not found')
    if result['value'] == 'bad_index':
        return fail_json(CODE_MISSING_PARAM, 'Invalid parameter: songIndexToRemove')
    return _map_mutate_result(ok, message)


def delete_playlist(
    deps: SubsonicDeps,
    catalog: Dict[str, Any],
    anchor_id: str,
    playlist_id: Optional[str],
) -> JSONResponse:
    if not playlist_id:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
    result = {'value': 'noop'}

    def mutator(data: Dict[str, Any]) -> bool:
        status = anchor_merge.delete_manual_playlist(data, playlist_id)
        result['value'] = status
        return status == 'ok'

    ok, message = deps.mutate_anchor(anchor_id, mutator)
    if result['value'] == 'forbidden':
        return fail_json(CODE_UNAUTHORIZED, 'Cannot delete the like playlist')
    if result['value'] == 'not_found':
        return fail_json(CODE_NOT_FOUND, 'Playlist not found')
    return _map_mutate_result(ok, message)
