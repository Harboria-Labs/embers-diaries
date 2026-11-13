"""Opaque observation sessions. The cookie is not the access code.

Sessions live beside the store, not in memory records. Each file is named by the
SHA-256 of a random token; the token itself is only returned in an HttpOnly
cookie. A session is valid only while its grant is still valid, and never past
that grant's own expiry. Revoking the grant deletes the sessions that point at it.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import deque
from pathlib import Path

AGENT_COOKIE = 'ember_observe_agent'
NS_COOKIE = 'ember_observe_ns'
_DUMMY = '0' * 64
_WINDOW = 60.0
FAILURE_LIMIT = 8
_failures = {}
_guard = threading.Lock()


def directory(db):
    root = Path(db._path).resolve()
    return root.parent / (root.name + '-observation-sessions')


def _save(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
            json.dump(data, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _digest(token):
    # Fixed-size input so a huge cookie cannot change the hash cost.
    raw = token if isinstance(token, str) and len(token) == 43 else ''
    return hashlib.sha256(raw.encode()).hexdigest()


def issue(db, *, kind, grant_id, expires_at, namespace=None):
    if kind not in ('observer', 'visualizer'):
        raise ValueError('Unknown observation session')
    if not isinstance(grant_id, str) or not grant_id:
        raise ValueError('Grant required')
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    _save(directory(db) / (digest + '.json'), {
        'v': 1, 'token_sha256': digest, 'kind': kind, 'grant_id': grant_id,
        'namespace': namespace, 'expires_at': float(expires_at), 'created_at': now,
    })
    return token


def resolve(db, token):
    digest = _digest(token)
    path = directory(db) / (digest + '.json')
    try:
        data = json.loads(path.read_text())
        stored = data.get('token_sha256', '') if isinstance(data, dict) else ''
    except (OSError, ValueError):
        data = None
        stored = ''
    try:
        match = hmac.compare_digest(stored or _DUMMY, digest)
    except (TypeError, ValueError):
        match = False
    if not match or not isinstance(data, dict) or data.get('expires_at', 0) <= time.time():
        raise PermissionError('Observation session expired or revoked')
    if data.get('kind') not in ('observer', 'visualizer') or not isinstance(data.get('grant_id'), str):
        raise PermissionError('Observation session expired or revoked')
    return data


def invalidate_grant(db, grant_id):
    root = directory(db)
    if not isinstance(grant_id, str) or not grant_id or not root.is_dir():
        return
    for path in root.glob('*.json'):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get('grant_id') == grant_id:
            path.unlink(missing_ok=True)


def client_key(request):
    forwarded = request.headers.get('x-forwarded-for') if request is not None else None
    if forwarded:
        hop = forwarded.split(',')[0].strip()
        if hop and len(hop) <= 80 and all(c.isalnum() or c in '.:' for c in hop):
            return hop
    client = getattr(request, 'client', None)
    return client.host if client and client.host else 'local'


def _prune(key, now):
    queued = _failures.get(key)
    if not queued:
        return None
    while queued and now - queued[0] > _WINDOW:
        queued.popleft()
    if not queued:
        _failures.pop(key, None)
        return None
    return queued


class AttemptLimited(Exception):
    """Too many failed code attempts from one client. The code is not part of this error."""


def begin_attempt(request):
    key = client_key(request)
    if too_many(key):
        raise AttemptLimited()
    return key


def too_many(key):
    now = time.monotonic()
    with _guard:
        queued = _prune(key, now)
        return bool(queued and len(queued) >= FAILURE_LIMIT)


def note_failure(key):
    now = time.monotonic()
    with _guard:
        queued = _prune(key, now) or _failures.setdefault(key, deque())
        queued.append(now)
        return len(queued) >= FAILURE_LIMIT


def clear_failures(key):
    with _guard:
        _failures.pop(key, None)


def cookie_secure(request):
    forwarded = (request.headers.get('x-forwarded-proto') or '').split(',')[0].strip().lower()
    if forwarded:
        return forwarded == 'https'
    return request.url.scheme == 'https'


def cookie_kwargs(request, expires_at):
    return {
        'max_age': max(0, int(float(expires_at) - time.time())),
        'httponly': True,
        'secure': cookie_secure(request),
        'samesite': 'lax',
        'path': '/',
    }
