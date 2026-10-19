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
# Universal-newline decoding keeps the served asset identical to the Git blob
# when a Windows checkout has converted its line endings to CRLF.
TRANSPORT = (ROOT / 'observer_transport.js').read_text(encoding='utf-8').encode('utf-8')
THEME = (ROOT / 'visualizer_theme.css').read_text(encoding='utf-8')
DASHBOARD = (ROOT / 'research_dashboard.js').read_text(encoding='utf-8')
STATUS_MONITOR = (ROOT / 'status_monitor.js').read_text(encoding='utf-8')
DASHBOARD_STYLE = (ROOT / 'research_dashboard.css').read_text(encoding='utf-8')
LOBBY_OBSERVER = (ROOT / 'lobby_observer.js').read_text(encoding='utf-8')
LOBBY_PANEL = '''<section id="lobbyView" hidden><div class="section-title"><b>Live Lobby · delegated shared task</b><span id="lobbyState">NOT CONNECTED</span></div><div id="lobbyScope" class="notice"></div><div class="filters" id="lobbyFilters"><input data-filter="agent_id" placeholder="Agent" aria-label="Filter agent"><input data-filter="task" placeholder="Task" aria-label="Filter task"><input data-filter="namespace" placeholder="Namespace" aria-label="Filter namespace"><select data-filter="status" aria-label="Filter status"><option value="">All statuses</option><option>active</option><option>withdrawn</option><option>expired</option><option>closed</option></select><select data-filter="kind" aria-label="Filter finding type"><option value="">All finding types</option><option>finding</option><option>failure</option><option>question</option><option>outcome</option></select></div><div id="lobbyItems"></div><div id="lobbyNotice" class="notice">Open a delegated Lobby observation to see shared working memories.</div></section>'''
LOBBY_STYLE = '''<style>#lobbyView{padding:16px;max-height:calc(100dvh - 130px);overflow:auto}#lobbyView[hidden],#observerWorkspace[hidden],#filters[hidden]{display:none}.lobby-card{padding:12px 16px;margin:8px 0;border:1px solid var(--line,#345);border-radius:10px;background:var(--panel,#15212b)}.lobby-card h3{margin:0 0 8px}.lobby-card p{margin:4px 0}.lobby-card small{display:block;opacity:.8;margin:3px 0}#lobbyState{font:inherit}</style>'''
NAMESPACE_TEMPLATE = (ROOT / 'visualizer.html').read_text(encoding='utf-8')
TEMPLATE = (ROOT / 'observer.html').read_text(encoding='utf-8')
TRANSPORT_SHA256 = hashlib.sha256(TRANSPORT).hexdigest()
BASELINE_SHA256 = '6b6d262295d56d9f71546a6b79f6eeb0f0caded00fc242c5b9cd28cf1e4274e9'
CLIENT_VERSION = 'shared-transport-v3 / sha256:' + TRANSPORT_SHA256[:12]
ASSET_URL = '/v1/observer/transport.js?v=' + TRANSPORT_SHA256
_files = ('observer_build.py', 'observer_routes.py', 'usefulness_routes.py',
          'status_monitor.js', 'server_status.html', 'research_dashboard.js', 'research_dashboard.css', 'lobby_observer.js', 'observer.html', 'visualizer.html', 'visualizer_theme.css', 'observer_transport.js', '../integration/research_observer.py')
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


def render(namespace=False):
    label = f'Observer client: {CLIENT_VERSION} · Server build: {SERVER_BUILD[:23]}'
    integrity = 'sha256-' + base64.b64encode(bytes.fromhex(TRANSPORT_SHA256)).decode()
    page = ((NAMESPACE_TEMPLATE if namespace else TEMPLATE).replace('__OBSERVER_BUILD_LABEL__', html.escape(label))
            .replace('__OBSERVER_BUILD_JSON__', json.dumps(metadata()).replace('<', '\\u003c'))
            .replace('__OBSERVER_ASSET_URL__', html.escape(ASSET_URL, quote=True))
            .replace('__OBSERVER_ASSET_INTEGRITY__', integrity).replace('__VISUALIZER_THEME__', THEME).replace('__RESEARCH_DASHBOARD__', '<style>'+DASHBOARD_STYLE+'</style><script>'+DASHBOARD+'</script><script>'+STATUS_MONITOR+'</script>'+('' if namespace else '<script>'+LOBBY_OBSERVER+'</script>')))
    if namespace:
        return page
    return (page.replace('<button id="themeToggle"', '<button id="lobbyToggle" hidden>Live Lobby</button><button id="themeToggle"', 1)
            .replace('<div class="workspace">', '<div class="workspace" id="observerWorkspace">', 1)
            .replace('<div class="overlay" id="access">', LOBBY_STYLE+LOBBY_PANEL+'<div class="overlay" id="access">', 1)
            .replace('grant=data;', 'grant=data;window.EmberLobbyObserver?.open(grant);', 1))
