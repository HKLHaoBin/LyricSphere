# GET-only /rest dispatcher. Folia 5f1c966 uses query params, never POST form.

import hashlib
import random
import time
from typing import Any, Dict, Optional

from fastapi.responses import JSONResponse

from subsonic import anchor_merge
from subsonic import auth as subsonic_auth
from subsonic import catalog as cat
from subsonic import lyrics as lyric_mod
from subsonic import mutations
from subsonic import playlists as plist
from subsonic import stream as media
from subsonic.deps import SubsonicDeps
from subsonic.envelope import (
    CODE_GENERIC,
    CODE_MISSING_PARAM,
    CODE_NOT_FOUND,
    CODE_UNAUTHORIZED,
    fail_json,
    ok_json,
)
from subsonic.params import get_one, get_int, get_list

MUTATIONS = frozenset({
    'createuser',
    'updateuser',
    'deleteuser',
    'changepassword',
    'setrating',
    'createbookmark',
    'deletebookmark',
    'createshare',
    'deleteshare',
})

MUSIC_FOLDER = {'id': '1', 'name': 'Famyliam'}


def _endpoint_name(endpoint: str) -> str:
    name = endpoint or ''
    if name.endswith('.view'):
        name = name[:-5]
    return name.strip('/')


def _user_payload(username: str) -> Dict[str, Any]:
    return {
        'user': {
            'username': username,
            'streamRole': True,
            'adminRole': False,
            'settingsRole': False,
            'downloadRole': False,
            'playlistRole': True,
            'coverArtRole': False,
            'commentRole': False,
            'podcastRole': False,
            'jukeboxRole': False,
            'shareRole': False,
            'videoConversionRole': False,
            'folder': [1],
        }
    }


def _find_song_by_artist_title(catalog: Dict[str, Any], artist: str, title: str) -> Optional[Dict[str, Any]]:
    artist_cf = (artist or '').strip().casefold()
    title_cf = (title or '').strip().casefold()
    if not title_cf:
        return None
    for song in catalog['songsById'].values():
        if (song.get('title') or '').casefold() != title_cf:
            continue
        if not artist_cf or artist_cf in (song.get('artist') or '').casefold():
            return song
    return None


def _parse_scrobble_submission(args) -> bool:
    raw = get_one(args, 'submission')
    if raw is None:
        return True
    return str(raw).strip().lower() not in ('false', '0', 'no')


def _parse_scrobble_time_ms(args):
    raw = get_one(args, 'time')
    if raw is None:
        return int(time.time() * 1000), None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, fail_json(CODE_MISSING_PARAM, 'Invalid parameter: time')
    if value < 0:
        return None, fail_json(CODE_MISSING_PARAM, 'Invalid parameter: time')
    return value, None


def dispatch(deps: SubsonicDeps, endpoint: str):
    name = _endpoint_name(endpoint)
    key = name.lower()
    if not key:
        return fail_json(CODE_GENERIC, 'Unknown endpoint')

    auth_result = subsonic_auth.authenticate(deps, key)
    if isinstance(auth_result, JSONResponse):
        return auth_result

    args = deps.get_query()
    username = auth_result.get('username') or ''
    anchor_id = auth_result.get('anchor_id') or ''

    if key in MUTATIONS:
        return fail_json(CODE_UNAUTHORIZED, 'User is not authorized for this operation')

    if key == 'ping':
        return ok_json()

    if key == 'getlicense':
        return ok_json({'license': {'valid': True}})

    if key == 'getopensubsonicextensions':
        return ok_json({'openSubsonicExtensions': []})

    if key == 'getmusicfolders':
        return ok_json({'musicFolders': {'musicFolder': [MUSIC_FOLDER]}})

    if key == 'getuser':
        return ok_json(_user_payload(username))

    catalog = cat.get_catalog(deps)

    if key == 'star':
        return mutations.star_song(deps, catalog, anchor_id, get_one(args, 'id'))

    if key == 'unstar':
        return mutations.unstar_song(deps, catalog, anchor_id, get_one(args, 'id'))

    if key == 'scrobble':
        time_ms, time_error = _parse_scrobble_time_ms(args)
        if time_error is not None:
            return time_error
        return mutations.scrobble_song(
            deps,
            catalog,
            anchor_id,
            get_one(args, 'id'),
            time_ms,
            _parse_scrobble_submission(args),
        )

    if key == 'createplaylist':
        return mutations.create_playlist(
            deps,
            catalog,
            anchor_id,
            get_one(args, 'name'),
            get_list(args, 'songId'),
            has_replace_id=bool(get_one(args, 'playlistId') or get_one(args, 'id')),
            username=username,
        )

    if key == 'updateplaylist':
        return mutations.update_playlist(
            deps,
            catalog,
            anchor_id,
            get_one(args, 'playlistId'),
            rename_to=get_one(args, 'name'),
            song_ids_to_add=get_list(args, 'songIdToAdd'),
            song_indexes_to_remove=get_list(args, 'songIndexToRemove'),
        )

    if key == 'deleteplaylist':
        return mutations.delete_playlist(
            deps,
            catalog,
            anchor_id,
            get_one(args, 'id'),
        )

    if key == 'getalbumlist2':
        list_type = (get_one(args, 'type') or 'alphabeticalByName').strip()
        size = get_int(args, 'size', 10, minimum=0, maximum=500)
        offset = get_int(args, 'offset', 0, minimum=0)
        if list_type == 'recent':
            listen_times = {}
            anchor = deps.read_anchor(anchor_id)
            if anchor and isinstance(anchor.get('data'), dict):
                file_times = anchor_merge.last_listen_ms_by_filename(anchor['data'])
                listen_times = cat.album_listen_times_from_files(catalog, file_times)
            albums = cat.sorted_albums(catalog, 'recent', listen_times=listen_times)
        elif list_type == 'newest':
            albums = cat.sorted_albums(catalog, 'newest')
        else:
            albums = cat.sorted_albums(catalog, 'alpha')
        page = cat.paginate(albums, offset, size)
        return ok_json({
            'albumList2': {
                'album': [cat.public_album(row, catalog, with_songs=False) for row in page],
            }
        })

    if key == 'getalbum':
        album_id = get_one(args, 'id')
        if not album_id:
            return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
        album = catalog['albums'].get(album_id)
        if not album:
            return fail_json(CODE_NOT_FOUND, 'Album not found')
        return ok_json({'album': cat.public_album(album, catalog, with_songs=True)})

    if key == 'getsong':
        song_id = get_one(args, 'id')
        if not song_id:
            return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
        song = catalog['songsById'].get(song_id)
        if not song:
            return fail_json(CODE_NOT_FOUND, 'Song not found')
        return ok_json({'song': cat.public_song(song)})

    if key == 'getartists':
        artists = cat.sorted_artists(catalog)
        return ok_json({
            'artists': {
                'ignoredArticles': 'The El La Los Las Le Les',
                'index': cat.artist_index_buckets(artists),
            }
        })

    if key == 'getartist':
        artist_id = get_one(args, 'id')
        if not artist_id:
            return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
        artist = catalog['artists'].get(artist_id)
        if not artist:
            return fail_json(CODE_NOT_FOUND, 'Artist not found')
        return ok_json({'artist': cat.public_artist(artist, catalog, with_albums=True)})

    if key == 'search3':
        query = get_one(args, 'query') or ''
        artist_count = get_int(args, 'artistCount', 20, minimum=0, maximum=500)
        album_count = get_int(args, 'albumCount', 20, minimum=0, maximum=500)
        song_count = get_int(args, 'songCount', 20, minimum=0, maximum=500)
        artist_offset = get_int(args, 'artistOffset', 0, minimum=0)
        album_offset = get_int(args, 'albumOffset', 0, minimum=0)
        song_offset = get_int(args, 'songOffset', 0, minimum=0)
        artists, albums, songs = cat.search_catalog(catalog, query)
        return ok_json({
            'searchResult3': {
                'artist': [
                    cat.public_artist(row, catalog, with_albums=False)
                    for row in cat.paginate(artists, artist_offset, artist_count)
                ],
                'album': [
                    cat.public_album(row, catalog, with_songs=False)
                    for row in cat.paginate(albums, album_offset, album_count)
                ],
                'song': [
                    cat.public_song(row)
                    for row in cat.paginate(songs, song_offset, song_count)
                ],
            }
        })

    if key == 'getrandomsongs':
        size = get_int(args, 'size', 10, minimum=0, maximum=500)
        songs = list(catalog['songsById'].values())
        songs.sort(key=lambda row: row['id'])
        seed_src = '|'.join([
            username,
            get_one(args, 's') or '',
            str(len(songs)),
            str(catalog.get('songRevision')),
        ])
        rng = random.Random(hashlib.sha256(seed_src.encode('utf-8')).hexdigest())
        picked = list(songs)
        rng.shuffle(picked)
        picked = picked[:size]
        return ok_json({'randomSongs': {'song': [cat.public_song(row) for row in picked]}})

    if key == 'getplaylists':
        rows = plist.list_manual_playlists(deps, anchor_id, catalog)
        payload = []
        for row in rows:
            item = dict(row)
            item['owner'] = username
            item.pop('entry', None)
            payload.append(item)
        return ok_json({'playlists': {'playlist': payload}})

    if key == 'getplaylist':
        playlist_id = get_one(args, 'id')
        if not playlist_id:
            return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
        row = plist.get_playlist(deps, anchor_id, playlist_id, catalog, username)
        if not row:
            return fail_json(CODE_NOT_FOUND, 'Playlist not found')
        return ok_json({'playlist': row})

    if key == 'getstarred2':
        songs = plist.starred_songs(deps, anchor_id, catalog)
        return ok_json({'starred2': {'song': songs, 'album': [], 'artist': []}})

    if key == 'getlyrics':
        song_id = get_one(args, 'id')
        song = catalog['songsById'].get(song_id) if song_id else None
        if song is None:
            song = _find_song_by_artist_title(
                catalog,
                get_one(args, 'artist') or '',
                get_one(args, 'title') or '',
            )
        if song is None:
            return ok_json(lyric_mod.lyrics_payload({'artist': get_one(args, 'artist') or '', 'title': get_one(args, 'title') or ''}, None))
        xml_text = lyric_mod.ttml_for_song(deps, song)
        return ok_json(lyric_mod.lyrics_payload(song, xml_text))

    if key == 'getlyricsbysongid':
        song_id = get_one(args, 'id')
        if not song_id:
            return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
        song = catalog['songsById'].get(song_id)
        if song is None:
            return fail_json(CODE_NOT_FOUND, 'Song not found')
        xml_text = lyric_mod.ttml_for_song(deps, song)
        return ok_json(lyric_mod.structured_lyrics_payload(song, xml_text))

    if key == 'stream':
        song_id = get_one(args, 'id')
        if not song_id:
            return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
        return media.stream_response(deps, song_id)

    if key == 'getcoverart':
        cover_id = get_one(args, 'id')
        if not cover_id:
            return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: id')
        return media.cover_response(deps, cover_id)

    return fail_json(CODE_GENERIC, 'Unknown endpoint')


def register_subsonic_routes(app, deps: SubsonicDeps) -> None:
    @app.route('/rest/<path:endpoint>', methods=['GET'])
    def subsonic_rest(endpoint: str):
        return dispatch(deps, endpoint)
