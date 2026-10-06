"""Run Contract 01 against a live server, creating only isolated test records."""
import argparse
import json
import uuid
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:9200')
args = parser.parse_args()
namespace = 'context01-' + uuid.uuid4().hex
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
assert 'primary_context' in catalog['ember_write'], 'Server lacks Contract 01 write schema'
assert 'primary_context' in catalog['ember_recall'], 'Server lacks Contract 01 recall schema'
assert 'retrieval_context' in catalog['ember_feedback'], 'Server lacks Contract 01 feedback schema'
agent = tool('ember_register', name=namespace)
auth = {k: agent[k] for k in ('agent_id', 'token')}
session = tool('ember_start_session', task='Contract 01 isolated test', namespace=namespace)
auth['session_id'] = session['session_id']
ids = {}
for context in ['LADC', 'Pairing Matrix']:
    ids[context] = tool('ember_write', content='Ember reference for Contract 01.', subject='Ember',
        primary_context=context, namespace=namespace, room='task', memory_type='raw')['id']
    assert tool('ember_read', record_id=ids[context])['data']['primary_context'] == context
result = tool('ember_recall', query='Ember', namespace=namespace, primary_context='LADC')
assert result['primary_context'] == 'LADC'
assert set(ids.values()) <= {r['id'] for r in result['results']}
fid = tool('ember_feedback', memory_id=ids['Pairing Matrix'], outcome='useful',
    retrieval_context=result['primary_context'])['feedback_id']
assert tool('ember_read', record_id=fid)['data']['retrieval_context'] == 'LADC'
assert tool('ember_read', record_id=ids['Pairing Matrix'])['data']['primary_context'] == 'Pairing Matrix'
unset = tool('ember_recall', query='Ember', namespace=namespace, primary_context=None, inspect_context=True)
assert unset['primary_context'] is None
assert isinstance(tool('ember_recall', query='Ember', namespace=namespace), list)
request = urllib.request.Request(args.url.rstrip('/') + '/v1/memory/recall',
    data=json.dumps({'query': 'Ember', 'namespace': namespace, 'primary_context': 'LADC'}).encode(),
    headers={'Content-Type': 'application/json', 'X-Ember-Agent-Id': auth['agent_id'],
             'X-Ember-Token': auth['token'], 'X-Ember-Session-Id': auth['session_id']})
with urllib.request.urlopen(request, timeout=30) as response:
    assert json.load(response)['primary_context'] == 'LADC'
print('PASS: Contract 01 MCP + REST; context preservation, cross-context recall, feedback, unset and legacy behavior.')
print('Namespace:', namespace)
print('Memory IDs:', json.dumps(ids))
