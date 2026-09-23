const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { createUpdateService, isNewer, selectInstaller, trustedReleaseUrl, parseReleaseAssets } = require('../src/updates');

const asset = (name = 'ExcelManus Setup 1.9.0.exe') => ({ name, state: 'uploaded', browser_download_url: `https://github.com/kilolonion/excelmanus/releases/download/v1.9.0/${encodeURIComponent(name)}` });
const release = () => ({ tag_name: 'v1.9.0', html_url: 'https://github.com/kilolonion/excelmanus/releases/tag/v1.9.0', body: 'Release notes', assets: [asset()] });

test('version comparison handles numeric ordering, current prereleases and no downgrade', () => {
  assert.equal(isNewer('v1.10.0', '1.9.9'), true);
  assert.equal(isNewer('v1.9.0', '1.9.0-beta.1'), true);
  assert.equal(isNewer('v1.9.0', '1.9.0'), false);
  assert.equal(isNewer('v1.9.0', '2.0.0'), false);
  assert.throws(() => isNewer('not a version', '1.0.0'));
});

test('downloads select the right platform and only project release URLs', () => {
  assert.equal(selectInstaller([asset()], 'win32', 'x64')?.name, asset().name);
  assert.equal(selectInstaller([asset()], 'win32', 'arm64'), undefined);
  assert.equal(selectInstaller([asset('ExcelManus-1.9.0-arm64.dmg')], 'darwin', 'x64'), undefined);
  assert.equal(selectInstaller([asset('ExcelManus-1.9.0-arm64.dmg')], 'darwin', 'arm64')?.name, 'ExcelManus-1.9.0-arm64.dmg');
  assert.equal(selectInstaller([asset('ExcelManus-1.9.0.dmg')], 'darwin', 'x64')?.name, 'ExcelManus-1.9.0.dmg');
  assert.equal(selectInstaller([asset('ExcelManus-1.9.0-universal.dmg')], 'darwin', 'arm64')?.name, 'ExcelManus-1.9.0-universal.dmg');
  for (const url of ['https://evil.test/file.exe', 'file:///C:/program.exe', 'https://github.com/other/repo/releases/tag/v1', 'https://github.com@evil.test/kilolonion/excelmanus/releases/tag/v1']) {
    assert.equal(trustedReleaseUrl(url), null);
  }
});

test('parses trusted release-page assets with their published digests', () => {
  const html = `<li class="Box-row">
    <a href="/kilolonion/excelmanus/releases/download/v1.9.0/ExcelManus.Setup.1.9.0.exe"><span class="text-bold">ExcelManus.Setup.1.9.0.exe</span></a>
    <span class="Truncate-text">sha256:${'a'.repeat(64)}</span>
  </li>
  <li class="Box-row">
    <a href="https://evil.test/releases/download/v1.9.0/ExcelManus.Setup.1.9.0.exe"><span class="text-bold">bad.exe</span></a>
    <span class="Truncate-text">sha256:${'b'.repeat(64)}</span>
  </li>`;
  const assets = parseReleaseAssets(html, 'https://github.com/kilolonion/excelmanus/releases/tag/v1.9.0');
  assert.deepEqual(assets, [{
    name: 'ExcelManus.Setup.1.9.0.exe', state: 'uploaded',
    browser_download_url: 'https://github.com/kilolonion/excelmanus/releases/download/v1.9.0/ExcelManus.Setup.1.9.0.exe',
    digest: `sha256:${'a'.repeat(64)}`,
  }]);
});

test('checks share a request, expose missing assets, and invalidate failed downloads', async () => {
  let calls = 0;
  let result = release();
  let status = 200;
  const service = createUpdateService({ current: '1.8.0', platform: 'win32', arch: 'x64',
    fetchImpl: async () => { calls++; return { ok: status === 200, status, json: async () => result }; },
  });
  await assert.rejects(service.download(), /先检查更新/);
  const [first, second] = await Promise.all([service.check(), service.check()]);
  assert.equal(calls, 1);
  assert.deepEqual(first, second);
  assert.equal(first.hasUpdate, true);
  await assert.rejects(service.download(), /不支持自动更新/);
  result.assets = [];
  assert.equal((await service.check()).downloadUrl, null);
  await assert.rejects(service.download());
  status = 403;
  await assert.rejects(service.check(), /频率超限/);
  await assert.rejects(service.download());
  status = 404;
  await assert.rejects(service.check(), /尚未发布/);
  status = 200;
  result = { ...release(), prerelease: true };
  await assert.rejects(service.check(), /发布信息无效/);
});

test('falls back to the public release page when the REST API is rate limited', async () => {
  const digest = `sha256:${'c'.repeat(64)}`;
  const assetsHtml = `<li class="Box-row">
    <a href="/kilolonion/excelmanus/releases/download/v1.9.0/ExcelManus.Setup.1.9.0.exe"><span class="text-bold">ExcelManus.Setup.1.9.0.exe</span></a>
    <span class="Truncate-text">${digest}</span>
  </li>`;
  const calls = [];
  const response = (status, body = '', headers = {}) => ({
    status, ok: status >= 200 && status < 300,
    headers: { get: name => headers[name.toLowerCase()] || null },
    text: async () => body,
  });
  const service = createUpdateService({ current: '1.8.0', platform: 'win32', arch: 'x64',
    fetchImpl: async url => {
      calls.push(url);
      if (url.includes('api.github.com')) return response(429);
      if (url.endsWith('/releases/latest')) return response(302, '', { location: 'https://github.com/kilolonion/excelmanus/releases/tag/v1.9.0' });
      return response(200, assetsHtml);
    },
  });
  const result = await service.check();
  assert.equal(result.latest, '1.9.0');
  assert.equal(result.hasUpdate, true);
  assert.equal(result.installerName, 'ExcelManus.Setup.1.9.0.exe');
  assert.equal(result.downloadUrl, 'https://github.com/kilolonion/excelmanus/releases/download/v1.9.0/ExcelManus.Setup.1.9.0.exe');
  assert.deepEqual(calls, [
    'https://api.github.com/repos/kilolonion/excelmanus/releases/latest',
    'https://github.com/kilolonion/excelmanus/releases/latest',
    'https://github.com/kilolonion/excelmanus/releases/expanded_assets/v1.9.0',
  ]);
});

test('keeps the fallback asset for the verified download path', async () => {
  const body = Buffer.from('verified installer fixture');
  const digest = `sha256:${createHash('sha256').update(body).digest('hex')}`;
  const assetsHtml = `<li class="Box-row">
    <a href="/kilolonion/excelmanus/releases/download/v1.9.0/ExcelManus.Setup.1.9.0.exe"><span class="text-bold">ExcelManus.Setup.1.9.0.exe</span></a>
    <span class="Truncate-text">${digest}</span>
  </li>`;
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'excelmanus-update-'));
  const response = (status, text = '', headers = {}) => ({
    status, ok: status >= 200 && status < 300,
    headers: { get: name => headers[name.toLowerCase()] || null },
    text: async () => text,
  });
  const service = createUpdateService({ current: '1.8.0', platform: 'win32', arch: 'x64',
    downloadDirectory: () => directory, installImpl: async () => false,
    fetchImpl: async url => {
      if (url.includes('api.github.com')) return response(429);
      if (url.endsWith('/releases/latest')) return response(302, '', { location: 'https://github.com/kilolonion/excelmanus/releases/tag/v1.9.0' });
      if (url.includes('/releases/download/')) {
        return new Response(body, { status: 200, headers: {
          'content-type': 'application/octet-stream', 'content-length': String(body.length),
        } });
      }
      return response(200, assetsHtml);
    },
  });
  try {
    await service.check();
    const result = await service.download();
    assert.equal(result.phase, 'ready');
    assert.match(result.error, /退出已取消/);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test('installer names cannot escape the private download directory', () => {
  for (const name of ['ExcelManus Setup ../../bad.exe', 'ExcelManus Setup bad\\evil.exe', 'ExcelManus Setup x:evil.exe']) {
    assert.equal(selectInstaller([asset(name)], 'win32', 'x64'), undefined);
  }
});
