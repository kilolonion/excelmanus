const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { downloadInstaller, verifyInstaller, trustedAssetRedirect } = require('../src/update-download');
const { createUpdateService } = require('../src/updates');
const { installDownloadedUpdate } = require('../src/update-install');

const contents = Buffer.from('test installer bytes');
const asset = { name: 'ExcelManus Setup 1.9.0.exe', state: 'uploaded', size: contents.length,
  digest: `sha256:${createHash('sha256').update(contents).digest('hex')}`,
  browser_download_url: 'https://github.com/kilolonion/excelmanus/releases/download/v1.9.0/setup.exe' };
const release = { tag_name: 'v1.9.0', html_url: 'https://github.com/kilolonion/excelmanus/releases/tag/v1.9.0', assets: [asset] };

async function fixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'em-update-test-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  return { asset, directory, signal: new AbortController().signal, onProgress() {},
    fetchImpl: async () => new Response(contents) };
}

test('streams progress, follows GitHub asset redirect and verifies before handoff', async t => {
  const options = await fixture(t);
  const states = [];
  const urls = [];
  const file = await downloadInstaller({ ...options, onProgress: s => states.push(s), fetchImpl: async (url, init) => {
    urls.push(url);
    assert.equal(init.redirect, 'manual');
    return urls.length === 1 ? new Response(null, { status: 302, headers: { location: 'https://release-assets.githubusercontent.com/asset' } }) : new Response(contents);
  } });
  assert.equal(urls.length, 2);
  assert.equal(states[0].received, 0);
  assert.equal(states.at(-1).percent, 100);
  assert.equal(states.at(-1).received, contents.length);
  assert.deepEqual(await fs.readFile(file.filename), contents);
  await verifyInstaller(file);
  await fs.writeFile(file.filename, Buffer.alloc(contents.length));
  await assert.rejects(verifyInstaller(file), /损坏/);
});

for (const [label, change, match] of [
  ['truncated', { fetchImpl: async () => new Response(contents.subarray(0, 4)) }, /不完整/],
  ['oversize', { fetchImpl: async () => new Response(Buffer.alloc(100)) }, /超出/],
  ['digest mismatch', { asset: { ...asset, digest: `sha256:${'0'.repeat(64)}` } }, /校验失败/],
  ['HTML proxy page', { fetchImpl: async () => new Response('<html>login', { headers: { 'content-type': 'text/html' } }) }, /网页/],
  ['missing release integrity', { asset: { ...asset, size: null, digest: null } }, /缺少/],
  ['not found', { fetchImpl: async () => new Response(null, { status: 404 }) }, /HTTP 404/],
  ['HTTP size mismatch', { fetchImpl: async () => new Response(contents, { headers: { 'content-length': '999' } }) }, /大小/],
  ['untrusted redirect', { fetchImpl: async () => new Response(null, { status: 302, headers: { location: 'https://evil.test/payload' } }) }, /不受信任/],
  ['redirect loop', { fetchImpl: async () => new Response(null, { status: 302, headers: { location: asset.browser_download_url } }) }, /重定向异常/],
]) {
  test(`rejects ${label} and removes only the failed staging directory`, async t => {
    const options = await fixture(t);
    await fs.writeFile(path.join(options.directory, 'user.txt'), 'keep');
    await assert.rejects(downloadInstaller({ ...options, ...change }), match);
    assert.deepEqual(await fs.readdir(options.directory), ['user.txt']);
  });
}

test('rejects insecure or credential-bearing redirects', () => {
  for (const url of ['http://github.com/file', 'https://github.com:444/file', 'https://user@github.com/file', 'file:///tmp/a', 'https://github.com.evil.test/file']) {
    assert.equal(trustedAssetRedirect(url), false);
  }
});

test('unknown HTTP content length works with release size, and digest-only streams stay indeterminate', async t => {
  const states = [];
  const options = await fixture(t);
  const file = await downloadInstaller({ ...options, asset: { ...asset, size: null }, onProgress: s => states.push(s) });
  assert.ok(states.every(s => s.percent === null && s.total === null));
  assert.equal(file.size, contents.length);
});

test('idle timeout cleans up a stalled body', async t => {
  const options = await fixture(t);
  await assert.rejects(downloadInstaller({ ...options, idleTimeoutMs: 10, fetchImpl: async (_url, { signal }) => {
    return new Response(new ReadableStream({ start(controller) {
      signal.addEventListener('abort', () => controller.error(signal.reason), { once: true });
    } }));
  } }), /超时/);
  assert.deepEqual(await fs.readdir(options.directory), []);
});

for (const code of ['ENOSPC', 'EACCES', 'EROFS']) {
  test(`reports ${code} without starting installation`, async t => {
    const options = await fixture(t);
    t.mock.method(fs, 'open', async () => { throw Object.assign(new Error(code), { code }); });
    await assert.rejects(downloadInstaller(options), /磁盘空间|权限/);
  });
}

test('deduplicates downloads, cancels safely, retries and preserves a blocked ready installer', async t => {
  const options = await fixture(t);
  let cancelStarted;
  const started = new Promise(resolve => { cancelStarted = resolve; });
  let stalled = true;
  let transfers = 0;
  const installed = [];
  const events = [];
  const service = createUpdateService({ current: '1.8.1', platform: 'win32', arch: 'x64',
    downloadDirectory: () => options.directory, onStatus: state => events.push(state),
    installImpl: async file => { installed.push(file); return false; },
    fetchImpl: async (url, { signal }) => {
      if (url.includes('api.github.com')) return new Response(JSON.stringify(release));
      transfers++;
      if (!stalled) return new Response(contents);
      return new Response(new ReadableStream({ start(controller) {
        signal.addEventListener('abort', () => controller.error(signal.reason), { once: true });
        cancelStarted();
      } }));
    },
  });
  await service.check();
  const first = service.download();
  const duplicate = service.download();
  await started;
  service.cancel();
  await Promise.all([first, duplicate]);
  assert.equal(transfers, 1);
  assert.equal(service.status().phase, 'cancelled');
  assert.equal(installed.length, 0);
  stalled = false;
  await service.download();
  assert.equal(transfers, 2);
  assert.equal(service.status().phase, 'ready');
  assert.match(service.status().error, /退出已取消/);
  assert.equal(installed.length, 1);
  assert.ok(events.every((e, i) => i === 0 || e.revision > events[i - 1].revision));
  await fs.writeFile(installed[0], 'tampered');
  await assert.rejects(service.install(), /损坏/);
  assert.equal(installed.length, 1);
  assert.equal(service.status().phase, 'error');
});

test('an installation launch failure keeps the verified download available for retry', async t => {
  const options = await fixture(t);
  const service = createUpdateService({ current: '1.8.1', platform: 'win32', arch: 'x64', downloadDirectory: () => options.directory,
    fetchImpl: async url => new Response(url.includes('api.github.com') ? JSON.stringify(release) : contents),
    installImpl: async () => { throw new Error('OS rejected launch'); },
  });
  await service.check();
  await assert.rejects(service.download(), /OS rejected/);
  assert.equal(service.status().phase, 'ready');
  assert.match(service.status().error, /可重试/);
});

test('installer handoff order, cancelled close and recovery on failure', async () => {
  for (const scenario of ['success', 'cancel', 'stop-failure', 'launch-failure']) {
    const steps = [];
    const run = installDownloadedUpdate({
      closeWindows: async () => { steps.push('close'); return scenario !== 'cancel'; },
      stopServices: async () => { steps.push('stop'); if (scenario === 'stop-failure') throw new Error('stop'); },
      launch: async () => { steps.push('launch'); return scenario === 'launch-failure' ? 'launch' : ''; },
      recover: async () => { steps.push('recover'); }, exit: () => { steps.push('exit'); },
    });
    if (scenario.endsWith('failure')) await assert.rejects(run);
    else await run;
    assert.deepEqual(steps, scenario === 'success' ? ['close', 'stop', 'launch', 'exit'] : scenario === 'cancel' ? ['close']
      : scenario === 'stop-failure' ? ['close', 'stop', 'recover'] : ['close', 'stop', 'launch', 'recover']);
  }
});
