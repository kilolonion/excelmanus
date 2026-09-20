const { test } = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { MobilePairing, lanAddresses } = require('../../web/server/mobile-pairing.cjs');

const interfaces = () => ({ 'Wi-Fi': [{ family: 'IPv4', internal: false, address: '192.168.1.12' }] });
async function listen(server) { await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); return `http://127.0.0.1:${server.address().port}`; }
const close = server => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); });
async function fixture(t, options = {}) {
  const received = [];
  const backendToken = 'host-only-backend-token-never-shared';
  const backendServer = http.createServer((req, res) => {
    if (req.url === '/api/v1/health') { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify({ version: 'test', api_schema_version: 1, auth_required: true })); return; }
    received.push({ url: req.url, auth: req.headers.authorization });
    if (req.url === '/api/v1/events' && req.headers.authorization === `Bearer ${backendToken}`) {
      res.setHeader('Content-Type', 'text/event-stream'); res.write('data: connected\n\n'); return;
    }
    res.setHeader('Content-Type', 'application/json');
    res.statusCode = req.headers.authorization === `Bearer ${backendToken}` ? 200 : 401;
    res.end('[]');
  });
  const frontendServer = http.createServer((_req, res) => res.end('<!doctype html><title>test</title>'));
  const backend = await listen(backendServer), frontend = await listen(frontendServer);
  const pairing = new MobilePairing({ frontend, backend, backendToken, interfaces, host: '127.0.0.1', port: 0, ...options });
  t.after(async () => { await pairing.shutdown(); await close(backendServer); await close(frontendServer); });
  await pairing.issue();
  const endpoint = `http://127.0.0.1:${pairing.port}`;
  const request = async (route, input, credential = '', extra = {}) => {
    const response = await fetch(endpoint + '/__excelmanus_pairing/' + route, { method: 'POST', headers: { 'Content-Type': 'application/json', ...(credential ? { Authorization: `Bearer ${credential}` } : {}), ...extra }, body: JSON.stringify(input) });
    return { status: response.status, body: await response.json() };
  };
  const code = () => new URL(pairing.status().qr.value).searchParams.get('code');
  const claim = () => request('claim', { code: code(), name: '测试手机' });
  return { pairing, endpoint, request, code, claim, received, frontend, backend, backendToken };
}

test('network choices contain private IPv4 only, preferring physical adapters', () => {
  const entry = address => [{ family: 'IPv4', internal: false, address }];
  assert.deepEqual(lanAddresses({ vEthernet: entry('172.22.1.1'), WAN: entry('8.8.8.8'), VPN: entry('198.18.1.1'), 'Wi-Fi': entry('192.168.1.10'), docker: entry('10.1.1.1') }).map(n => n.name), ['Wi-Fi', 'vEthernet', 'docker']);
});

test('one scan, explicit approval, per-device credential and backend-token translation', async t => {
  const f = await fixture(t);
  const invite = f.code();
  assert.ok(!f.pairing.status().qr.value.includes(f.backendToken));
  const claimed = await f.claim();
  assert.equal(claimed.status, 200);
  assert.match(claimed.body.comparison, /^\d{6}$/);
  assert.equal((await f.request('claim', { code: invite })).status, 410);
  const { id, pollToken } = claimed.body;
  assert.equal((await f.request('status', { id }, 'wrong')).status, 410);
  assert.deepEqual((await f.request('status', { id }, pollToken)).body, { state: 'pending' });
  assert.equal((await fetch(f.endpoint + '/api/v1/sessions')).status, 401);
  f.pairing.approve(id);
  const approved = (await f.request('status', { id }, pollToken)).body;
  assert.equal(approved.state, 'approved'); assert.notEqual(approved.token, f.backendToken);
  assert.ok(!JSON.stringify(f.pairing.status()).includes(approved.token));
  assert.equal((await fetch(f.endpoint + '/api/v1/sessions?manage_token=' + approved.token)).status, 200);
  assert.equal(f.received.at(-1).auth, `Bearer ${f.backendToken}`);
  assert.equal(f.received.at(-1).url, '/api/v1/sessions');
  assert.equal((await fetch(f.endpoint + '/api/mobile-pairing', { headers: { Authorization: `Bearer ${approved.token}` } })).status, 403);
  await f.request('ack', { id }, pollToken);
  assert.equal((await f.request('status', { id }, pollToken)).status, 410);
  f.pairing.revoke(f.pairing.devices[0].id);
  assert.equal((await fetch(f.endpoint + '/api/v1/sessions', { headers: { Authorization: `Bearer ${approved.token}` } })).status, 401);
});

test('expired, refreshed, rejected and cancelled invitations never bind a phone', async t => {
  let now = Date.now();
  const f = await fixture(t, { now: () => now });
  const oldCode = f.code(); now += 180001;
  assert.equal((await f.request('claim', { code: oldCode })).status, 410);
  await f.pairing.issue(); const superseded = f.code(); await f.pairing.issue();
  assert.equal((await f.request('claim', { code: superseded })).status, 410);
  const { id, pollToken } = (await f.claim()).body;
  f.pairing.reject(id);
  assert.equal((await f.request('status', { id }, pollToken)).body.state, 'rejected');
  assert.throws(() => f.pairing.approve(id));
  await f.pairing.issue(); const pending = (await f.claim()).body;
  await f.request('cancel', { id: pending.id }, pending.pollToken);
  assert.throws(() => f.pairing.approve(pending.id));
  assert.equal(f.pairing.devices.length, 0);
});

test('revoking a phone also disconnects its already-open data stream', async t => {
  const f = await fixture(t);
  const pending = (await f.claim()).body; f.pairing.approve(pending.id);
  const token = (await f.request('status', { id: pending.id }, pending.pollToken)).body.token;
  const response = await fetch(f.endpoint + '/api/v1/events', { headers: { Authorization: `Bearer ${token}` }, signal: AbortSignal.timeout(5000) });
  const reader = response.body.getReader();
  assert.match(new TextDecoder().decode((await reader.read()).value), /connected/);
  f.pairing.revoke(f.pairing.devices[0].id);
  await assert.rejects(reader.read());
});

test('binding hashes persist across restart; disabling does not silently re-enable', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'excelmanus-pairing-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const stateFile = path.join(directory, 'state.json');
  const f = await fixture(t, { stateFile });
  const pending = (await f.claim()).body; f.pairing.approve(pending.id);
  const token = (await f.request('status', { id: pending.id }, pending.pollToken)).body.token;
  assert.ok(!fs.readFileSync(stateFile, 'utf8').includes(token));
  const port = f.pairing.port; await f.pairing.shutdown();
  const restored = new MobilePairing({ frontend: f.frontend, backend: f.backend, backendToken: f.backendToken, stateFile, interfaces, host: '127.0.0.1' });
  t.after(() => restored.shutdown());
  assert.equal(restored.enabled, true); await restored.start(); assert.equal(restored.port, port);
  assert.equal((await fetch(f.endpoint + '/api/v1/sessions', { headers: { Authorization: `Bearer ${token}` } })).status, 200);
  await restored.stop();
  assert.equal(JSON.parse(fs.readFileSync(stateFile, 'utf8')).enabled, false);
});

test('browser-origin and oversized pairing requests are rejected without consuming the code', async t => {
  const f = await fixture(t);
  assert.equal((await f.request('claim', { code: f.code() }, '', { Origin: 'http://evil.example' })).status, 403);
  assert.equal((await f.request('claim', { code: f.code(), name: 'a'.repeat(5000) })).status, 413);
  assert.equal((await f.claim()).status, 200);
});

test('QR refresh cannot race an explicit stop into reopening the listener', async t => {
  const f = await fixture(t);
  await f.pairing.stop();
  const starting = f.pairing.issue();
  const stopping = f.pairing.stop();
  await Promise.allSettled([starting, stopping]);
  assert.equal(f.pairing.status().enabled, false);
});
