# Subsonic token auth: u + t=md5(password+salt) + s, or p=plaintext.
# Logs never include u/t/s/password. Failures are rate-limited per IP+username.

import hashlib
import hmac
import threading
import time
from typing import Optional, Tuple

from subsonic import credentials as creds
from subsonic.deps import SubsonicDeps
from subsonic.envelope import CODE_AUTH, CODE_MISSING_PARAM, fail_json
from subsonic.params import get_one

_AUTH_RATE_LOCK = threading.Lock()
_AUTH_FAILURES = {}
_AUTH_WINDOW_SEC = 60
_AUTH_MAX_FAILURES = 20

# getOpenSubsonicExtensions may be called without credentials (OpenSubsonic spec).
ANON_ENDPOINTS = frozenset({'getopensubsonicextensions'})


def reset_rate_limit() -> None:
    with _AUTH_RATE_LOCK:
        _AUTH_FAILURES.clear()


def _md5_hex(text: str) -> str:
    return hashlib.md5(text.encode('utf-8')).hexdigest()


def _rate_allow(ip: str, username: str) -> bool:
    key = (ip or 'unknown', username or '')
    now = time.time()
    with _AUTH_RATE_LOCK:
        bucket = [ts for ts in _AUTH_FAILURES.get(key, []) if now - ts < _AUTH_WINDOW_SEC]
        if len(bucket) >= _AUTH_MAX_FAILURES:
            _AUTH_FAILURES[key] = bucket
            return False
        return True


def _rate_fail(ip: str, username: str) -> None:
    key = (ip or 'unknown', username or '')
    now = time.time()
    with _AUTH_RATE_LOCK:
        bucket = [ts for ts in _AUTH_FAILURES.get(key, []) if now - ts < _AUTH_WINDOW_SEC]
        bucket.append(now)
        _AUTH_FAILURES[key] = bucket


def _passwords_match(expected: str, given: str) -> bool:
    if expected is None or given is None:
        return False
    return hmac.compare_digest(expected.encode('utf-8'), given.encode('utf-8'))


def authenticate(deps: SubsonicDeps, endpoint_name: str):
    name = (endpoint_name or '').lower()
    args = deps.get_query()
    username = get_one(args, 'u') or ''
    token = get_one(args, 't')
    salt = get_one(args, 's')
    plain = get_one(args, 'p')
    ip = deps.get_remote_addr() or ''

    if name in ANON_ENDPOINTS:
        return {'username': username or None, 'anchor_id': None, 'anonymous': True}

    if not username:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: u')
    if not token and not plain:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: t or p')
    if token and not salt:
        return fail_json(CODE_MISSING_PARAM, 'Required parameter is missing: s')

    if not _rate_allow(ip, username):
        deps.logger.warning('subsonic auth rate limited username=%s', username)
        return fail_json(CODE_AUTH, 'Wrong username or password')

    entry = creds.find_user(deps.credentials_path, username)
    if not entry:
        _rate_fail(ip, username)
        deps.logger.info('subsonic auth failed username=%s reason=unknown_user', username)
        return fail_json(CODE_AUTH, 'Wrong username or password')

    secret = deps.get_secret()
    password = creds.decrypt_user_password(entry, secret)
    if not password:
        _rate_fail(ip, username)
        deps.logger.info('subsonic auth failed username=%s reason=decrypt', username)
        return fail_json(CODE_AUTH, 'Wrong username or password')

    ok = False
    if token and salt:
        expected = _md5_hex(password + salt)
        ok = hmac.compare_digest(expected, token.lower())
    elif plain:
        if plain.startswith('enc:'):
            try:
                decoded = bytes.fromhex(plain[4:]).decode('utf-8')
            except Exception:
                decoded = ''
            ok = _passwords_match(password, decoded)
        else:
            ok = _passwords_match(password, plain)

    if not ok:
        _rate_fail(ip, username)
        deps.logger.info('subsonic auth failed username=%s reason=bad_token', username)
        return fail_json(CODE_AUTH, 'Wrong username or password')

    anchor_id = entry.get('anchor_id') or ''
    if not deps.anchor_exists(anchor_id):
        _rate_fail(ip, username)
        deps.logger.info('subsonic auth failed username=%s reason=missing_anchor', username)
        return fail_json(CODE_AUTH, 'Wrong username or password')

    deps.logger.info('subsonic auth ok username=%s', username)
    return {
        'username': username,
        'anchor_id': anchor_id,
        'anonymous': False,
        'credential_rev': entry.get('credential_rev'),
    }
