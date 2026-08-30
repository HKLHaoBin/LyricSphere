# Subsonic application-password store.
# Passwords are stored with HMAC-authenticated keystream encryption so
# md5(password+salt) can be replayed. Never write plaintext JSON.

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

USERNAME_MAX = 128
PASSWORD_MAX = 128
ANCHOR_ID_RE = re.compile(r'^[0-9a-f]{64}$')

_LOCK = threading.Lock()


def _derive_key(secret: bytes) -> bytes:
    return hashlib.sha256(b'famyliam-subsonic-v1' + secret).digest()


def encrypt_password(plaintext: str, secret: bytes) -> str:
    key = _derive_key(secret)
    nonce = os.urandom(16)
    data = plaintext.encode('utf-8')
    keystream = b''
    counter = 0
    while len(keystream) < len(data):
        keystream += hashlib.sha256(key + nonce + counter.to_bytes(4, 'big')).digest()
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(data, keystream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(nonce + tag + cipher).decode('ascii')


def decrypt_password(blob: str, secret: bytes) -> Optional[str]:
    try:
        raw = base64.urlsafe_b64decode(blob.encode('ascii'))
    except Exception:
        return None
    if len(raw) < 48:
        return None
    nonce = raw[:16]
    tag = raw[16:48]
    cipher = raw[48:]
    key = _derive_key(secret)
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        return None
    keystream = b''
    counter = 0
    while len(keystream) < len(cipher):
        keystream += hashlib.sha256(key + nonce + counter.to_bytes(4, 'big')).digest()
        counter += 1
    data = bytes(a ^ b for a, b in zip(cipher, keystream))
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return None


def _empty_store() -> Dict[str, Any]:
    return {'users': {}}


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp.write_text(data, encoding='utf-8')
    os.replace(str(tmp), str(path))


def load_store(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return _empty_store()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return _empty_store()
    if not isinstance(raw, dict):
        return _empty_store()
    users = raw.get('users')
    if not isinstance(users, dict):
        users = {}
    return {'users': users}


def public_user(entry: Dict[str, Any], username: str) -> Dict[str, Any]:
    return {
        'username': username,
        'anchor_id': entry.get('anchor_id'),
        'credential_rev': entry.get('credential_rev', 0),
        'created_at': entry.get('created_at'),
        'updated_at': entry.get('updated_at'),
    }


def validate_username(username: str) -> Optional[str]:
    if not username or not str(username).strip():
        return 'username is required'
    if len(username) > USERNAME_MAX:
        return 'username is too long'
    if any(ch in username for ch in ['\n', '\r', '\0']):
        return 'username is invalid'
    return None


def validate_password(password: str) -> Optional[str]:
    if not password:
        return 'app_password is required'
    if len(password) > PASSWORD_MAX:
        return 'app_password is too long'
    return None


def validate_anchor_id(anchor_id: str) -> Optional[str]:
    if not ANCHOR_ID_RE.fullmatch(anchor_id or ''):
        return 'anchor_id must be 64 lowercase hex characters'
    return None


def list_users(path: Path) -> List[Dict[str, Any]]:
    with _LOCK:
        store = load_store(path)
        rows = []
        for username, entry in store.get('users', {}).items():
            if isinstance(entry, dict):
                rows.append(public_user(entry, username))
        rows.sort(key=lambda row: row['username'])
        return rows


def create_user(
    path: Path,
    secret: bytes,
    username: str,
    app_password: str,
    anchor_id: str,
    now_iso: str,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    err = validate_username(username) or validate_password(app_password) or validate_anchor_id(anchor_id)
    if err:
        return False, err, None
    with _LOCK:
        store = load_store(path)
        users = store['users']
        if username in users:
            return False, 'username already exists', None
        entry = {
            'username': username,
            'anchor_id': anchor_id,
            'password_blob': encrypt_password(app_password, secret),
            'credential_rev': 1,
            'created_at': now_iso,
            'updated_at': now_iso,
        }
        users[username] = entry
        _atomic_write(path, store)
        return True, 'created', public_user(entry, username)


def rotate_user(
    path: Path,
    secret: bytes,
    username: str,
    app_password: str,
    now_iso: str,
) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    err = validate_username(username) or validate_password(app_password)
    if err:
        return False, err, None
    with _LOCK:
        store = load_store(path)
        users = store['users']
        entry = users.get(username)
        if not isinstance(entry, dict):
            return False, 'user not found', None
        old_rev = int(entry.get('credential_rev') or 0)
        entry['password_blob'] = encrypt_password(app_password, secret)
        entry['credential_rev'] = old_rev + 1
        entry['updated_at'] = now_iso
        users[username] = entry
        _atomic_write(path, store)
        return True, 'rotated', public_user(entry, username)


def revoke_user(path: Path, username: str) -> Tuple[bool, str]:
    err = validate_username(username)
    if err:
        return False, err
    with _LOCK:
        store = load_store(path)
        users = store['users']
        if username not in users:
            return False, 'user not found'
        del users[username]
        _atomic_write(path, store)
        return True, 'revoked'


def find_user(path: Path, username: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        store = load_store(path)
        entry = store['users'].get(username)
        if isinstance(entry, dict):
            return dict(entry)
        return None


def decrypt_user_password(entry: Dict[str, Any], secret: bytes) -> Optional[str]:
    blob = entry.get('password_blob')
    if not isinstance(blob, str):
        return None
    return decrypt_password(blob, secret)
