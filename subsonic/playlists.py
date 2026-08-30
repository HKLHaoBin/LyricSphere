# Read user playlists from the bound anchor. Artist shortcuts are excluded.

from typing import Any, Dict, List, Optional

from subsonic.catalog import public_song
from subsonic.deps import SubsonicDeps


def _playlists_from_anchor(anchor: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(anchor, dict):
        return []
    data = anchor.get('data') if isinstance(anchor.get('data'), dict) else {}
    deleted = data.get('deletedPlaylistIds') if isinstance(data.get('deletedPlaylistIds'), dict) else {}
    raw = data.get('playlists')
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        playlist_type = str(item.get('type') or 'manual')
        if playlist_type == 'artist':
            continue
        playlist_id = item.get('id')
        if not playlist_id:
            continue
        if str(playlist_id) in deleted:
            continue
        tracks = item.get('tracks')
        if not isinstance(tracks, list):
            tracks = []
        seen = set()
        unique_tracks = []
        for name in tracks:
            text = str(name) if name else ''
            if not text or text in seen:
                continue
            seen.add(text)
            unique_tracks.append(text)
        out.append({
            'id': str(playlist_id),
            'name': str(item.get('name') or item.get('title') or playlist_id),
            'type': playlist_type,
            'tracks': unique_tracks,
        })
    return out


def list_manual_playlists(deps: SubsonicDeps, anchor_id: str, catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    anchor = deps.read_anchor(anchor_id)
    created = ''
    if isinstance(anchor, dict):
        created = str(anchor.get('saved_at') or '')
        if not created and anchor.get('mtime'):
            created = str(anchor.get('mtime'))
    rows = []
    for item in _playlists_from_anchor(anchor):
        songs = _songs_for_tracks(item['tracks'], catalog)
        duration = sum(int(song.get('duration') or 0) for song in songs)
        row = {
            'id': item['id'],
            'name': item['name'],
            'comment': '',
            'owner': '',
            'public': False,
            'songCount': len(songs),
            'duration': duration,
            'created': created,
            'changed': created,
            'entry': songs,
        }
        if songs:
            row['coverArt'] = songs[0]['id']
        rows.append(row)
    return rows


def _songs_for_tracks(tracks: List[str], catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    songs = []
    by_filename = catalog.get('songsByFilename') or {}
    by_id = catalog.get('songsById') or {}
    for filename in tracks:
        song_id = by_filename.get(filename)
        if not song_id:
            continue
        song = by_id.get(song_id)
        if song:
            songs.append(public_song(song))
    return songs


def get_playlist(deps: SubsonicDeps, anchor_id: str, playlist_id: str, catalog: Dict[str, Any], owner: str) -> Optional[Dict[str, Any]]:
    for row in list_manual_playlists(deps, anchor_id, catalog):
        if row['id'] == playlist_id:
            row['owner'] = owner
            return row
    return None


def starred_songs(deps: SubsonicDeps, anchor_id: str, catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    for row in list_manual_playlists(deps, anchor_id, catalog):
        if row['id'] == 'like':
            return list(row.get('entry') or [])
    return []
