# Injected backend capabilities. This package must not import backend.py.

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class SubsonicDeps:
    get_query: Callable[[], Any]
    get_remote_addr: Callable[[], Optional[str]]
    get_header: Callable[[str], Optional[str]]
    copy_index_snapshot: Callable[[], Dict[str, Any]]
    normalize_audio_ref: Callable[[Optional[str]], Optional[str]]
    resolve_songs_path: Callable[[str], Path]
    resolve_static_json: Callable[[str], Path]
    read_static_json: Callable[[str], Optional[Dict[str, Any]]]
    artist_entries: Callable[[Dict[str, Any]], List[Tuple[str, str]]]
    lys_to_ttml_text: Callable[..., Tuple[bool, str]]
    lrc_to_ttml_text: Callable[..., Tuple[bool, str]]
    read_anchor: Callable[[str], Optional[Dict[str, Any]]]
    anchor_exists: Callable[[str], bool]
    mutate_anchor: Callable[[str, Callable[[Dict[str, Any]], bool]], Tuple[bool, str]]
    credentials_path: Path
    get_secret: Callable[[], bytes]
    logger: Any
    guess_audio_mimetype: Callable[[Path], str]
    guess_image_mimetype: Callable[[Path], str]
