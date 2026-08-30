# Build a Folia-facing music catalog from Famyliam index snapshots.
# External HTTP audio is excluded. Empty albums are one-song-one-album.
# Lock is held only by copy_index_snapshot; file reads happen here.

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from subsonic.deps import SubsonicDeps
from subsonic.ids import assign_id

_AUDIO_SUFFIX_MIME = {
    '.mp3': ('mp3', 'audio/mpeg'),
    '.wav': ('wav', 'audio/wav'),
    '.ogg': ('ogg', 'audio/ogg'),
    '.flac': ('flac', 'audio/flac'),
    '.m4a': ('m4a', 'audio/mp4'),
    '.aac': ('aac', 'audio/aac'),
    '.opus': ('opus', 'audio/opus'),
    '.webm': ('webm', 'audio/webm'),
    '.mp4': ('mp4', 'audio/mp4'),
}

_CACHE_LOCK = threading.Lock()
_CACHE = {'revs': None, 'catalog': None}


def _iso_mtime(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        ts = path.stat().st_mtime
    except OSError:
        ts = 0
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _mtime_value(path: Optional[Path]) -> float:
    if path is None:
        return 0.0
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _file_size(path: Optional[Path]) -> int:
    if path is None:
        return 0
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _suffix_mime(path: Optional[Path]) -> Tuple[str, str]:
    if path is None:
        return ('', 'application/octet-stream')
    suffix = path.suffix.lower()
    return _AUDIO_SUFFIX_MIME.get(suffix, (suffix.lstrip('.'), 'application/octet-stream'))


def _cover_local_path(deps: SubsonicDeps, album_img: str) -> Optional[Path]:
    relative = deps.normalize_audio_ref(album_img)
    if not relative:
        return None
    try:
        path = deps.resolve_songs_path(relative)
    except Exception:
        return None
    if path.is_file():
        return path
    return None


def _meta_lyric_refs(meta: Dict[str, Any]):
    raw = str((meta or {}).get('lyrics') or '').strip()
    if not raw or raw == '!':
        return '', ''
    parts = raw.split('::')
    if len(parts) >= 4:
        return (parts[1] or '').strip(), (parts[2] or '').strip()
    return raw, ''


def _audio_local_path(deps: SubsonicDeps, song_value: str) -> Optional[Path]:
    relative = deps.normalize_audio_ref(song_value)
    if not relative:
        return None
    try:
        path = deps.resolve_songs_path(relative)
    except Exception:
        return None
    if path.is_file():
        return path
    return None


def _duration_seconds(summary: Dict[str, Any], json_data: Optional[Dict[str, Any]]) -> int:
    for source in (summary, json_data or {}, (json_data or {}).get('meta') or {}):
        if not isinstance(source, dict):
            continue
        if source.get('duration_ms') is not None:
            try:
                return max(0, int(int(source['duration_ms']) / 1000))
            except (TypeError, ValueError):
                pass
        if source.get('duration') is not None:
            try:
                value = float(source['duration'])
                if value > 10000:
                    return max(0, int(value / 1000))
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
    return 0


def _optional_int(source: Dict[str, Any], *keys: str):
    for key in keys:
        if source.get(key) is None or source.get(key) == '':
            continue
        try:
            return int(source[key])
        except (TypeError, ValueError):
            continue
    return None


def _album_group_key(album_name: str, first_artist_key: str, filename: str) -> str:
    if album_name.strip():
        return album_name.casefold() + '\0' + first_artist_key.casefold()
    return '\0' + first_artist_key.casefold() + '\0' + filename


def build_catalog(snapshot: Dict[str, Any], deps: SubsonicDeps) -> Dict[str, Any]:
    songs_in = snapshot.get('songs') or {}
    taken = {}
    songs_by_id = {}
    songs_by_filename = {}
    albums = {}
    artists = {}
    album_song_ids = {}
    artist_album_ids = {}
    artist_display = {}

    for filename, summary in songs_in.items():
        if not isinstance(summary, dict):
            continue
        audio_path = _audio_local_path(deps, str(summary.get('song') or ''))
        if audio_path is None:
            continue
        json_path = None
        json_data = None
        try:
            json_path = deps.resolve_static_json(filename)
            json_data = deps.read_static_json(filename)
        except Exception:
            json_path = None
            json_data = None
        meta = {}
        if isinstance(json_data, dict) and isinstance(json_data.get('meta'), dict):
            meta = json_data['meta']

        entries = deps.artist_entries(summary)
        if not entries:
            entries = [('__unknown_artist__', 'Unknown artist')]
        first_key, first_display = entries[0]
        artist_joined = ', '.join(display for _key, display in entries)
        album_raw = str(summary.get('album') or meta.get('album') or '').strip()
        title = str(summary.get('title') or meta.get('title') or filename)
        album_display = album_raw or title
        group_key = _album_group_key(album_raw, first_key, filename)

        song_id = assign_id('s_', filename, taken)
        album_id = assign_id('al_', group_key, taken)
        artist_id = assign_id('ar_', first_key, taken)

        suffix, content_type = _suffix_mime(audio_path)
        cover_src = str(summary.get('albumImgSrc') or meta.get('albumImgSrc') or '')
        lyrics_src = str(summary.get('lyricsPath') or '').strip()
        trans_src = str(summary.get('translationPath') or '').strip()
        if not lyrics_src or lyrics_src == '!':
            meta_lyrics, meta_trans = _meta_lyric_refs(meta)
            if meta_lyrics and meta_lyrics != '!':
                lyrics_src = meta_lyrics
            if (not trans_src or trans_src == '!') and meta_trans and meta_trans != '!':
                trans_src = meta_trans
        cover_path = _cover_local_path(deps, cover_src)
        cover_relative = deps.normalize_audio_ref(cover_src) if cover_path is not None else None
        duration = _duration_seconds(summary, json_data)
        created = _iso_mtime(json_path)
        created_ts = _mtime_value(json_path) or float(summary.get('mtime') or 0)
        track = _optional_int(meta, 'track', 'trackNumber')
        year = _optional_int(meta, 'year')
        genre = str(meta.get('genre') or '').strip() or None

        song = {
            'id': song_id,
            'filename': filename,
            'title': title,
            'album': album_display,
            'artist': artist_joined,
            'albumId': album_id,
            'artistId': artist_id,
            'coverArt': song_id,
            'hasCoverFile': cover_relative is not None,
            'coverRelative': cover_relative,
            'size': _file_size(audio_path),
            'contentType': content_type,
            'suffix': suffix,
            'duration': duration,
            'path': deps.normalize_audio_ref(str(summary.get('song') or '')) or audio_path.name,
            'audioRelative': deps.normalize_audio_ref(str(summary.get('song') or '')) or audio_path.name,
            'playCount': 0,
            'created': created,
            'createdTs': created_ts,
            'type': 'music',
            'isDir': False,
            'isVideo': False,
            'lyricsPath': lyrics_src,
            'translationPath': trans_src,
        }
        if track is not None:
            song['track'] = track
        if year is not None:
            song['year'] = year
        if genre:
            song['genre'] = genre

        songs_by_id[song_id] = song
        songs_by_filename[filename] = song_id
        album_song_ids.setdefault(album_id, []).append(song_id)

        if album_id not in albums:
            albums[album_id] = {
                'id': album_id,
                'name': album_display,
                'artist': first_display,
                'artistId': artist_id,
                'coverArt': song_id if cover_relative is not None else None,
                'created': created,
                'createdTs': created_ts,
                'year': year,
                'groupKey': group_key,
            }
        else:
            album_row = albums[album_id]
            if cover_relative is not None and not album_row.get('coverArt'):
                album_row['coverArt'] = song_id
            if created_ts and (album_row.get('createdTs') or 0) > created_ts:
                album_row['created'] = created
                album_row['createdTs'] = created_ts
            if year is not None and album_row.get('year') is None:
                album_row['year'] = year

        for artist_key, artist_name in entries:
            aid = assign_id('ar_', artist_key, taken)
            artist_display.setdefault(aid, artist_name)
            artist_album_ids.setdefault(aid, [])
            if album_id not in artist_album_ids[aid]:
                artist_album_ids[aid].append(album_id)
            if aid not in artists:
                artists[aid] = {
                    'id': aid,
                    'name': artist_name,
                    'coverArt': None,
                    'artistKey': artist_key,
                }

    for album_id, song_ids in album_song_ids.items():
        unique_ids = list(dict.fromkeys(song_ids))
        unique_ids.sort(key=lambda sid: (
            songs_by_id[sid].get('track') or 0,
            songs_by_id[sid]['title'].casefold(),
            sid,
        ))
        album_song_ids[album_id] = unique_ids
        songs = [songs_by_id[sid] for sid in unique_ids]
        albums[album_id]['songCount'] = len(songs)
        albums[album_id]['duration'] = sum(int(row.get('duration') or 0) for row in songs)
        if not albums[album_id].get('coverArt'):
            for row in songs:
                if row.get('hasCoverFile'):
                    albums[album_id]['coverArt'] = row['id']
                    break

    for aid, album_ids in artist_album_ids.items():
        unique_albums = list(dict.fromkeys(album_ids))
        unique_albums.sort(key=lambda alid: (albums[alid]['name'].casefold(), alid))
        artist_album_ids[aid] = unique_albums
        artists[aid]['albumCount'] = len(unique_albums)

    return {
        'songRevision': snapshot.get('song_revision'),
        'artistRevision': snapshot.get('artist_revision'),
        'songsById': songs_by_id,
        'songsByFilename': songs_by_filename,
        'albums': albums,
        'artists': artists,
        'albumSongIds': album_song_ids,
        'artistAlbumIds': artist_album_ids,
    }


def get_catalog(deps: SubsonicDeps) -> Dict[str, Any]:
    snapshot = deps.copy_index_snapshot()
    revs = (snapshot.get('song_revision'), snapshot.get('artist_revision'))
    with _CACHE_LOCK:
        if _CACHE['catalog'] is not None and _CACHE['revs'] == revs:
            return _CACHE['catalog']
    catalog = build_catalog(snapshot, deps)
    with _CACHE_LOCK:
        _CACHE['revs'] = revs
        _CACHE['catalog'] = catalog
        return catalog


def reset_catalog_cache() -> None:
    with _CACHE_LOCK:
        _CACHE['revs'] = None
        _CACHE['catalog'] = None


def public_song(song: Dict[str, Any], include_filename: bool = False) -> Dict[str, Any]:
    row = {
        'id': song['id'],
        'title': song['title'],
        'album': song['album'],
        'artist': song['artist'],
        'coverArt': song['coverArt'],
        'size': song['size'],
        'contentType': song['contentType'],
        'suffix': song['suffix'],
        'duration': song['duration'],
        'path': song.get('audioRelative') or song.get('path'),
        'playCount': song.get('playCount', 0),
        'created': song['created'],
        'albumId': song['albumId'],
        'artistId': song['artistId'],
        'type': 'music',
        'isDir': False,
        'isVideo': False,
    }
    for key in ('track', 'year', 'genre'):
        if song.get(key) is not None:
            row[key] = song[key]
    return row


def public_album(album: Dict[str, Any], catalog: Dict[str, Any], with_songs: bool = False) -> Dict[str, Any]:
    row = {
        'id': album['id'],
        'name': album['name'],
        'artist': album['artist'],
        'artistId': album['artistId'],
        'songCount': album.get('songCount', 0),
        'duration': album.get('duration', 0),
        'created': album.get('created'),
    }
    if album.get('coverArt'):
        row['coverArt'] = album['coverArt']
    if album.get('year') is not None:
        row['year'] = album['year']
    if with_songs:
        ids = catalog['albumSongIds'].get(album['id'], [])
        row['song'] = [public_song(catalog['songsById'][sid]) for sid in ids if sid in catalog['songsById']]
    return row


def public_artist(artist: Dict[str, Any], catalog: Dict[str, Any], with_albums: bool = False) -> Dict[str, Any]:
    row = {
        'id': artist['id'],
        'name': artist['name'],
        'albumCount': artist.get('albumCount', 0),
    }
    if artist.get('coverArt'):
        row['coverArt'] = artist['coverArt']
    if with_albums:
        ids = catalog['artistAlbumIds'].get(artist['id'], [])
        row['album'] = [
            public_album(catalog['albums'][alid], catalog, with_songs=False)
            for alid in ids
            if alid in catalog['albums']
        ]
    return row


def paginate(items: List[Any], offset: int, size: int) -> List[Any]:
    if offset < 0:
        offset = 0
    if size < 0:
        size = 0
    return items[offset:offset + size]


def album_listen_times_from_files(
    catalog: Dict[str, Any],
    file_times: Optional[Dict[str, int]],
) -> Dict[str, int]:
    """Map albumId -> max listen ms using per-filename listen times."""
    if not file_times:
        return {}
    out: Dict[str, int] = {}
    for song in catalog.get('songsById', {}).values():
        filename = song.get('filename')
        if not filename or filename not in file_times:
            continue
        album_id = song.get('albumId')
        if not album_id:
            continue
        ms = int(file_times[filename])
        prev = out.get(album_id)
        if prev is None or ms > prev:
            out[album_id] = ms
    return out


def sorted_albums(
    catalog: Dict[str, Any],
    kind: str,
    listen_times: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    albums = list(catalog['albums'].values())
    if kind == 'recent':
        times = listen_times or {}
        scored = []
        for row in albums:
            ms = times.get(row['id'])
            if ms is None:
                continue
            scored.append((row, int(ms)))
        scored.sort(key=lambda item: (-item[1], item[0]['name'].casefold(), item[0]['id']))
        return [row for row, _ in scored]
    if kind == 'newest':
        albums.sort(key=lambda row: (-float(row.get('createdTs') or 0), row['name'].casefold(), row['id']))
    else:
        albums.sort(key=lambda row: (row['name'].casefold(), row['id']))
    return albums


def sorted_artists(catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    artists = list(catalog['artists'].values())
    artists.sort(key=lambda row: (row['name'].casefold(), row['id']))
    return artists


def artist_index_buckets(artists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets = {}
    order = []
    for artist in artists:
        name = artist.get('name') or '#'
        first = name[0]
        if first.isascii() and first.isalpha():
            letter = first.upper()
        else:
            letter = '#'
        if letter not in buckets:
            buckets[letter] = []
            order.append(letter)
        buckets[letter].append({
            'id': artist['id'],
            'name': artist['name'],
            'albumCount': artist.get('albumCount', 0),
        })
    order.sort(key=lambda letter: (letter == '#', letter))
    return [{'name': letter, 'artist': buckets[letter]} for letter in order]


def search_catalog(catalog: Dict[str, Any], query: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    needle = (query or '').strip().casefold()
    artists = []
    albums = []
    songs = []
    if not needle:
        return artists, albums, songs
    for artist in catalog['artists'].values():
        if needle in artist['name'].casefold():
            artists.append(artist)
    for album in catalog['albums'].values():
        if needle in album['name'].casefold() or needle in (album.get('artist') or '').casefold():
            albums.append(album)
    for song in catalog['songsById'].values():
        blob = ' '.join([
            song.get('title') or '',
            song.get('album') or '',
            song.get('artist') or '',
        ]).casefold()
        if needle in blob:
            songs.append(song)
    artists.sort(key=lambda row: (row['name'].casefold(), row['id']))
    albums.sort(key=lambda row: (row['name'].casefold(), row['id']))
    songs.sort(key=lambda row: (row['title'].casefold(), row['id']))
    return artists, albums, songs
