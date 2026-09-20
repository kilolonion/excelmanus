const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../app/src/main/assets/bridge.js'), 'utf8');

function setup({ origin = 'https://work.example', mainFrame = true, rejectChunk = false, clipboard } = {}) {
  const calls = [];
  const storage = new Map();
  const handlers = new Map();
  const timers = new Set();
  const context = {
    location: { origin },
    navigator: { clipboard },
    __EXCELMANUS_ANDROID_CONFIG__: { origin: 'https://work.example', token: 'test-only-management-token' },
    sessionStorage: { setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) },
    document: { addEventListener: (name, handler) => handlers.set(name, handler) },
    addEventListener: (name, handler) => handlers.set(name, handler),
    setTimeout: (fn, time) => { const timer = setTimeout(fn, time); timers.add(timer); return timer; },
    clearTimeout: (timer) => { clearTimeout(timer); timers.delete(timer); },
    Blob, Uint8Array, Date, Map, Promise, Error,
    btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
    fetch: async () => ({ blob: async () => new Blob(['legacy']) }),
    ExcelManusNative: {
      postMessage: (raw) => {
        const call = JSON.parse(raw);
        calls.push(call);
        queueMicrotask(() => context.ExcelManusNative.onmessage({ data: JSON.stringify({
          requestId: call.requestId,
          ok: !(rejectChunk && call.type === 'saveChunk'),
          error: 'destination unavailable',
        }) }));
      },
    },
  };
  context.window = context;
  context.top = mainFrame ? context : {};
  vm.runInNewContext(source, context);
  return { context, calls, storage, handlers, timers };
}

async function until(predicate) {
  for (let i = 0; i < 100; i++) {
    if (predicate()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  assert.fail('native bridge did not settle');
}

test('bootstrap precedes page scripts and pins all API traffic to the selected origin', () => {
  const { context, storage } = setup();
  assert.equal(storage.get('excelmanus_manage_token'), 'test-only-management-token');
  assert.equal(context.__EXCELMANUS_ANDROID_CONFIG__, undefined);
  context.__EXCELMANUS_RUNTIME__ = { backendOrigin: 'http://localhost:54321' };
  assert.equal(context.__EXCELMANUS_RUNTIME__.backendOrigin, 'same-origin');
  assert.equal(context.excelManusAndroid.version, 1);
});

test('does not expose credentials or a bridge to other origins or subframes', () => {
  for (const opts of [{ origin: 'https://evil.example' }, { mainFrame: false }]) {
    const { context, storage } = setup(opts);
    assert.equal(context.excelManusAndroid, undefined);
    assert.equal(storage.size, 0);
  }
});

test('saves Chinese named binary files with bounded, acknowledged chunks', async () => {
  const { context, calls, timers } = setup();
  const bytes = Buffer.alloc(130000);
  for (let i = 0; i < bytes.length; i++) bytes[i] = i % 256;
  context.excelManusAndroid.saveBlob(new Blob([bytes]), '本月结果.xlsx');
  await until(() => calls.some((call) => call.type === 'saveFinish') && timers.size === 0);
  assert.equal(calls[0].filename, '本月结果.xlsx');
  assert.equal(calls[0].size, bytes.length);
  const chunks = calls.filter((call) => call.type === 'saveChunk');
  assert.deepEqual(chunks.map((call) => call.sequence), [0, 1, 2]);
  assert.ok(chunks.every((call) => call.data.length <= 65536));
  assert.deepEqual(Buffer.concat(chunks.map((call) => Buffer.from(call.data, 'base64'))), bytes);
});

test('aborts failed transfers and reports a visible error instead of downloading twice', async () => {
  const { context, calls, timers } = setup({ rejectChunk: true });
  context.excelManusAndroid.saveBlob(new Blob(['content']), 'x.xlsx');
  await until(() => calls.some((call) => call.type === 'notice') && timers.size === 0);
  assert.ok(calls.some((call) => call.type === 'saveAbort'));
  assert.ok(!calls.some((call) => call.type === 'saveFinish'));
  assert.equal(calls.find((call) => call.type === 'notice').message, 'destination unavailable');
});

test('handles blob anchors from older server versions', async () => {
  const { calls, handlers, timers } = setup();
  let prevented = false;
  handlers.get('click')({ target: { closest: () => ({ href: 'blob:https://work.example/id', download: '旧版结果.xlsx' }) }, preventDefault: () => { prevented = true; } });
  await until(() => calls.some((call) => call.type === 'saveFinish') && timers.size === 0);
  assert.ok(prevented);
  assert.equal(calls[0].filename, '旧版结果.xlsx');
});

test('copies text through a write-only bridge when the browser clipboard is unavailable', async () => {
  const { context, calls, timers } = setup();
  await context.navigator.clipboard.writeText('手机复制：123456');
  assert.equal(calls[0].type, 'copyText');
  assert.equal(calls[0].text, '手机复制：123456');
  assert.equal(context.navigator.clipboard.readText, undefined);
  assert.equal(timers.size, 0);
  await assert.rejects(context.excelManusAndroid.copyText('x'.repeat(16385)), /16,384/);
  assert.equal(calls.length, 1);
});

test('preserves the secure-context browser clipboard', () => {
  const clipboard = { writeText() {} };
  const { context } = setup({ clipboard });
  assert.equal(context.navigator.clipboard, clipboard);
});

test('back closes overlays then the mobile sidebar, and leaves desktop sidebars alone', () => {
  const { context } = setup();
  let closed = false;
  let escaped = false;
  const sidebar = { querySelector: () => ({ click: () => { closed = true; } }) };
  const overlay = { dispatchEvent: (event) => { escaped = event.key === 'Escape' && event.bubbles; } };
  context.KeyboardEvent = function (name, options) { Object.assign(this, options); };
  context.getComputedStyle = () => ({ position: 'fixed' });
  context.document.querySelector = (selector) => selector.startsWith('aside') ? sidebar : overlay;
  assert.equal(context.excelManusAndroid.handleBack(), true);
  assert.ok(escaped);
  assert.equal(closed, false);
  context.document.querySelector = (selector) => selector.startsWith('aside') ? sidebar : null;
  assert.equal(context.excelManusAndroid.handleBack(), true);
  assert.ok(closed);
  context.getComputedStyle = () => ({ position: 'relative' });
  assert.equal(context.excelManusAndroid.handleBack(), false);
});
