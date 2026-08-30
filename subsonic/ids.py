# Stable Subsonic ids with collision detection.
# Prefixes: s_ song, ar_ artist, al_ album.

import hashlib
from typing import Callable, Dict, Optional

DigestFn = Callable[[bytes], str]


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def assign_id(prefix: str, key: str, taken: Dict[str, str], digest_fn: Optional[DigestFn] = None) -> str:
    """Return prefix+hex for key. Never silently overwrite another key.

    Starts at 16 hex chars, then grows. If the full digest collides, appends _n.
    taken maps assigned id -> original key.
    """
    if not key:
        key = ''
    digest = (digest_fn or _sha256_hex)(key.encode('utf-8'))
    n = 16
    while n <= len(digest):
        candidate = prefix + digest[:n]
        owner = taken.get(candidate)
        if owner is None:
            taken[candidate] = key
            return candidate
        if owner == key:
            return candidate
        n += 2
    extra = 0
    while True:
        candidate = prefix + digest + '_' + str(extra)
        owner = taken.get(candidate)
        if owner is None:
            taken[candidate] = key
            return candidate
        if owner == key:
            return candidate
        extra += 1
