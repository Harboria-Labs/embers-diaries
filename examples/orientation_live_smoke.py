"""Run Contract 02 against a live server, creating only isolated test records."""
import argparse
import json
import uuid
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:9200')
args = parser.parse_args()
namespace = 'context02-' + uuid.uuid4().hex
counter = 0
auth = {}


def rpc(method, params):
    global counter
    counter += 1
    request = urllib.request.Request(args.url.rstrip('/') + '/mcp',
        data=json.dumps({'jsonrpc': '2.0', 'id': counter, 'method': method, 'params': params}).encode(),
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        value = json.load(response)
    assert 'error' not in value, 'JSON-RPC error'
    return value['result']


def tool(tool_name, **fields):
    result = rpc('tools/call', {'name': tool_name, 'arguments': {**auth, **fields}})
    assert not result.get('isError'), result['content'][0]['text']
    return json.loads(result['content'][0]['text'])


catalog = {t['name']: t['inputSchema']['properties'] for t in rpc('tools/list', {})['tools']}
assert 'ember_orient' in catalog, 'Server lacks Contract 02'
agent = tool('ember_register', name=namespace)
auth = {k: agent[k] for k in ('agent_id', 'token')}
session = tool('ember_start_session', task='Contract 02 isolated test', namespace=namespace)
auth['session_id'] = session['session_id']
ids = {}
for context, content in [('mathematics', 'latent reactivation query heat'), ('experiments', 'old memories reactivation trial')]:
    ids[context] = tool('ember_write', content=content, subject='LADC',
        primary_context=context, namespace=namespace, room='task', memory_type='raw')['id']
request_body = dict(clues='latent reactivation query heat old memories', namespace=namespace,
                    limits={'subjects': 2, 'contexts': 3, 'records': 4})
result = tool('ember_orient', **request_body)
assert result['territory'][0]['subject'] == 'LADC'
assert {c['primary_context'] for c in result['territory'][0]['contexts']} == set(ids)
assert set(result['returned_ids']) == set(ids.values())
chosen = result['territory'][0]['contexts'][0]['primary_context']
recalled = tool('ember_recall', query='reactivation', namespace=namespace, primary_context=chosen)
assert recalled['primary_context'] == chosen
assert tool('ember_orient', clues='zzzzneverstored', namespace=namespace)['territory'] == []
request = urllib.request.Request(args.url.rstrip('/') + '/v1/memory/orient',
    data=json.dumps(request_body).encode(),
    headers={'Content-Type': 'application/json', 'X-Ember-Agent-Id': auth['agent_id'],
             'X-Ember-Token': auth['token'], 'X-Ember-Session-Id': auth['session_id']})
with urllib.request.urlopen(request, timeout=30) as response:
    assert json.load(response) == result
print('PASS: Contract 02 MCP + REST, rough-clue discovery, distinct subcontexts, empty result and Contract 01 handoff.')
print('Namespace:', namespace)
print('Memory IDs:', json.dumps(ids))
print('Orientation result:', json.dumps(result, ensure_ascii=False))
