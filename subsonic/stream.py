# stream and getCoverArt. Success is binary; failure is JSON envelope.
# format=raw and other transcoding values all return the original file.
# size= is ignored. Remote covers are treated as missing (no 302).

from subsonic.catalog import get_catalog
from subsonic.deps import SubsonicDeps
from subsonic.envelope import CODE_NOT_FOUND, fail_json
from fastapi.responses import FileResponse


def _safe_songs_file(deps: SubsonicDeps, relative: str):
    if not relative:
        return None
    try:
        path = deps.resolve_songs_path(relative)
    except Exception:
        return None
    if path.is_file():
        return path
    return None


def stream_response(deps: SubsonicDeps, media_id: str):
    if not media_id:
        return fail_json(CODE_NOT_FOUND, 'The requested media was not found')
    catalog = get_catalog(deps)
    song = catalog['songsById'].get(media_id)
    if not song:
        return fail_json(CODE_NOT_FOUND, 'The requested media was not found')
    path = _safe_songs_file(deps, song.get('audioRelative') or '')
    if path is None:
        return fail_json(CODE_NOT_FOUND, 'The requested media was not found')
    return FileResponse(path, media_type=deps.guess_audio_mimetype(path), filename=path.name)


def _cover_relative(catalog, media_id: str):
    song = catalog['songsById'].get(media_id)
    if song and song.get('coverRelative'):
        return song.get('coverRelative')
    album = catalog['albums'].get(media_id)
    if album and album.get('coverArt'):
        cover_song = catalog['songsById'].get(album['coverArt'])
        if cover_song:
            return cover_song.get('coverRelative')
    return None


def cover_response(deps: SubsonicDeps, media_id: str):
    if not media_id:
        return fail_json(CODE_NOT_FOUND, 'Cover art not found')
    catalog = get_catalog(deps)
    relative = _cover_relative(catalog, media_id)
    path = _safe_songs_file(deps, relative or '')
    if path is None:
        return fail_json(CODE_NOT_FOUND, 'Cover art not found')
    return FileResponse(path, media_type=deps.guess_image_mimetype(path))
