"""Read-only observer artifact identity; never inspect a memory store.

Capture assets once per process so replacing files under a running server cannot
silently mix fresh client assets with its already-imported observer routes.
The server fingerprint identifies observer source bytes, not the whole engine.
"""
import base64
import hashlib
import html
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).parent
TRANSPORT = (ROOT / 'observer_transport.js').read_bytes()
TEMPLATE = (ROOT / 'observer.html').read_text(encoding='utf-8')
TRANSPORT_SHA256 = hashlib.sha256(TRANSPORT).hexdigest()
BASELINE_SHA256 = '6b6d262295d56d9f71546a6b79f6eeb0f0caded00fc242c5b9cd28cf1e4274e9'
CLIENT_VERSION = ('9f94c5a / transport-v2' if TRANSPORT_SHA256 == BASELINE_SHA256
                  else 'unrecognized / sha256:' + TRANSPORT_SHA256[:12])
ASSET_URL = '/v1/observer/transport.js?v=' + TRANSPORT_SHA256
_files = ('observer_build.py', 'observer_routes.py', 'usefulness_routes.py',
          'observer.html', 'observer_transport.js', '../integration/research_observer.py')
_hash = hashlib.sha256()
for _name in _files:
    _bytes = (ROOT / _name).read_bytes()
    _hash.update(_name.encode() + b'\0' + str(len(_bytes)).encode() + b'\0' + _bytes)
SERVER_BUILD = 'sha256:' + _hash.hexdigest()
try:
    PACKAGE_VERSION = version('embers-diaries')
except PackageNotFoundError:
    PACKAGE_VERSION = 'uninstalled source checkout'


def metadata():
    return {'transport_client_version': CLIENT_VERSION,
            'transport_sha256': TRANSPORT_SHA256, 'transport_asset_url': ASSET_URL,
            'transport_baseline_commit': '9f94c5a01d4d0ca843cfdb3a322df5968671935f',
            'transport_matches_baseline': TRANSPORT_SHA256 == BASELINE_SHA256,
            'server_build': SERVER_BUILD, 'server_build_kind': 'observer-source-sha256',
            'server_git_commit': None, 'package_version': PACKAGE_VERSION}


def headers():
    return {'Cache-Control': 'no-store, no-cache, must-revalidate, no-transform',
            'X-Ember-Observer-Build': SERVER_BUILD,
            'X-Ember-Transport-SHA256': TRANSPORT_SHA256,
            'X-Ember-Transport-Version': CLIENT_VERSION}


def render():
    label = f'Observer client: {CLIENT_VERSION} · Server build: {SERVER_BUILD[:23]}'
    integrity = 'sha256-' + base64.b64encode(bytes.fromhex(TRANSPORT_SHA256)).decode()
    return (TEMPLATE.replace('__OBSERVER_BUILD_LABEL__', html.escape(label))
            .replace('__OBSERVER_BUILD_JSON__', json.dumps(metadata()).replace('<', '\\u003c'))
            .replace('__OBSERVER_ASSET_URL__', html.escape(ASSET_URL, quote=True))
            .replace('__OBSERVER_ASSET_INTEGRITY__', integrity))
