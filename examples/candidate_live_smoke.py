"""Exercise a running experimental server using its private credentials file."""
import argparse
import json
from pathlib import Path
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument('--url', default='http://127.0.0.1:9200')
parser.add_argument('--credentials', default='ember-experimental-credentials.json')
args = parser.parse_args()
auth = json.loads(Path(args.credentials).read_text())


def tool(name, **arguments):
    payload = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': name, 'arguments': {**auth, **arguments}}}
    request = urllib.request.Request(args.url.rstrip('/') + '/mcp',
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request) as response:
        result = json.load(response)
    assert 'error' not in result, result
    result = result['result']
    assert not result.get('isError'), result
    return json.loads(result['content'][0]['text'])

mid = tool('ember_write', content='Live experiment: a useful clue is not automatically true.', namespace='memories')['id']
request = dict(namespace='memories', context_id='live-test', query_id='smoke-' + mid,
               direct_scores={mid: 1}, elapsed=2)
first = tool('ember_candidate_recall', **request)
assert mid in first['selected_ids']
assert tool('ember_candidate_recall', **request) == first
fid = tool('ember_feedback', memory_id=mid, schema_version=2, channel='relevance',
    outcome='useful', outcome_id='outcome-' + mid, context_id='live-test',
    context={'task': 'live-test'}, signal=1)['feedback_id']
resolution = dict(namespace='memories', context_id='live-test', request_id='resolve-' + mid,
    expected_revision=0, decision={'outcome_id': 'outcome-' + mid, 'status': 'accepted',
        'credits': [{'memory_version': mid, 'value': 1}], 'report_ids': [fid], 'reason': 'observed contribution in live smoke test'})
receipt = tool('ember_resolve_relevance', **resolution)
assert tool('ember_resolve_relevance', **resolution) == receipt
state = tool('ember_relevance_state', namespace='memories', context_id='live-test')
assert state['memory_bias'][mid] == .25
headers = {'Content-Type': 'application/json', 'X-Ember-Agent-Id': auth['agent_id'], 'X-Ember-Token': auth['token']}
request.pop('namespace'); request.pop('context_id')
req = urllib.request.Request(args.url.rstrip('/') + '/v1/relevance/memories/live-test/recall',
    data=json.dumps(request).encode(), headers=headers)
with urllib.request.urlopen(req) as response:
    assert json.load(response) == first
print('PASS: HTTP MCP + REST recall, authenticated feedback, resolution, retry protection.')
print('Memory created:', mid)
print('Context tokens:', first['token_count'])
