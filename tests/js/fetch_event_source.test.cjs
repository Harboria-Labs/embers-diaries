// Parser test for the ngrok-capable SSE client. Not a browser layout check.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {ReadableStream} = require('node:stream/web');

const source = fs.readFileSync('embers/api/observer_transport.js', 'utf8');

function load(chunks, {status = 200, fail = false, url = '/v1/observer/stream?x=1'} = {}) {
  const requests = [];
  const events = [];
  const errors = [];
  const opens = [];
  function body() {
    let i = 0;
    return new ReadableStream({
      pull(controller) {
        if (i >= chunks.length) controller.close();
        else controller.enqueue(new TextEncoder().encode(chunks[i++]));
      },
    });
  }
  const sandbox = {
    window: {},
    fetch: async (url, options) => {
      requests.push({url, options});
      if (fail) throw Error('network down');
      return {ok: status === 200, status, body: status === 200 ? body() : null};
    },
    AbortController,
    TextDecoder,
    ReadableStream,
    URLSearchParams,
    setTimeout,
    clearTimeout,
  };
  vm.runInNewContext(source, sandbox);
  const sourceStream = new sandbox.window.EmberFetchEventSource(url);
  sourceStream.addEventListener('ready', event => events.push(['ready', event]));
  sourceStream.addEventListener('observer', event => events.push(['observer', event]));
  sourceStream.onopen = () => opens.push('open');
  sourceStream.onerror = error => errors.push(String(error && error.message || error));
  return {requests, events, errors, opens, sourceStream};
}

async function flush() {
  await new Promise(resolve => setTimeout(resolve, 30));
}

test('fetch stream sends the ngrok header and parses named SSE frames', async () => {
  const frame = [
    ': keepalive comment\n',
    'id: 7\n',
    'event: ready\n',
    'data: {"ok":true}\n',
    '\n',
    'event: observer\r\n',
    'data: {"cursor":\r\n',
    'data: 8}\r\n',
    '\r\n',
  ];
  const loaded = load(frame);
  await flush();
  assert.equal(loaded.requests.length, 1);
  assert.equal(loaded.requests[0].options.headers['ngrok-skip-browser-warning'], 'true');
  assert.equal(loaded.requests[0].options.headers.Accept, 'text/event-stream');
  assert.equal(loaded.requests[0].options.credentials, 'same-origin');
  assert.deepEqual(loaded.opens, ['open']);
  assert.equal(loaded.events[0][0], 'ready');
  assert.equal(loaded.events[0][1].lastEventId, '7');
  assert.equal(loaded.events[0][1].data, '{"ok":true}');
  assert.equal(loaded.events[1][0], 'observer');
  assert.equal(loaded.events[1][1].data, '{"cursor":\n8}');
  loaded.sourceStream.close();
});

test('http failure reports onerror and does not open', async () => {
  const loaded = load([], {status: 401});
  await flush();
  assert.deepEqual(loaded.opens, []);
  assert.match(loaded.errors[0], /SSE HTTP 401/);
  loaded.sourceStream.close();
});

test('resume sends Last-Event-ID from the after cursor', async () => {
  const loaded = load([': ping\n\n'], {url: '/v1/visualizer-stream/bench?after=4'});
  await flush();
  assert.equal(loaded.requests[0].options.headers['Last-Event-ID'], '4');
  loaded.sourceStream.close();
});
