import { test } from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { createGateway } from '../tools/lan-gateway.mjs';

const token = 'test-only-management-token';
async function listen(server) { server.listen(0, '127.0.0.1'); await once(server, 'listening'); return `http://127.0.0.1:${server.address().port}`; }
function cleanup(t, ...servers) { t.after(() => { for (const server of servers) { server.closeAllConnections(); server.close(); } }); }

test('rejects missing credentials and non-loopback upstreams', () => {
  assert.throws(() => createGateway({ token: '' }));
  assert.throws(() => createGateway({ token, backend: 'http://public.example.com' }));
});
test('protects APIs, preserves uploads, and tells clients authentication is required', async (t) => {
  const backend = http.createServer((req, res) => {
    if (req.url === '/api/v1/health') return res.end(JSON.stringify({ status: 'ok', version: '1.8.0', api_schema_version: 1, auth_required: false }));
    const chunks = [];
    req.on('data', (data) => chunks.push(data));
    req.on('end', () => { res.setHeader('x-auth-received', req.headers.authorization || ''); res.end(Buffer.concat(chunks)); });
  });
  const frontend = http.createServer((req, res) => { res.end(req.headers.authorization || 'frontend'); });
  const back = await listen(backend);
  const front = await listen(frontend);
  const gateway = createGateway({ token: ` ${token} `, backend: back, frontend: front });
  cleanup(t, backend, frontend, gateway);
  const base = await listen(gateway);
  assert.equal((await fetch(base + '/api/v1/sessions')).status, 401);
  for (const path of ['/api%2fv1/sessions', '/%61pi/v1/sessions', '/api%252fv1/sessions', '/API/v1/sessions']) {
    assert.equal((await fetch(base + path)).status, 401, path);
  }
  assert.equal((await fetch(base + '/api/v1/sessions', { headers: { Authorization: 'Bearer wrong' } })).status, 401);
  assert.equal((await (await fetch(base + '/api/v1/health')).json()).auth_required, true);
  const body = Buffer.from('中文文件\u0000\u00ff');
  const uploaded = await fetch(base + '/api/v1/upload', { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body });
  assert.deepEqual(Buffer.from(await uploaded.arrayBuffer()), body);
  assert.equal(uploaded.headers.get('x-auth-received'), `Bearer ${token}`);
  assert.equal(await (await fetch(base + '/', { headers: { Authorization: `Bearer ${token}` } })).text(), 'frontend');
});
test('delivers SSE before the upstream finishes', async (t) => {
  let finish;
  const backend = http.createServer((req, res) => {
    res.writeHead(200, { 'Content-Type': 'text/event-stream' });
    res.write('event: reply\ndata: {"text":"开始"}\n\n');
    finish = () => res.end('event: done\ndata: {}\n\n');
  });
  const target = await listen(backend);
  const gateway = createGateway({ token, backend: target, frontend: target });
  cleanup(t, backend, gateway);
  const base = await listen(gateway);
  const response = await fetch(base + '/api/v1/chat/stream', { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.match(new TextDecoder().decode(first.value), /开始/);
  finish();
  const last = await reader.read();
  assert.match(new TextDecoder().decode(last.value), /event: done/);
  assert.equal((await reader.read()).done, true);
});
