const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createUpdateService, isNewer, selectInstaller, trustedReleaseUrl } = require('../src/updates');

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

test('checks share a request, expose missing assets, and invalidate failed downloads', async () => {
  let calls = 0;
  let result = release();
  let status = 200;
  let opened;
  const service = createUpdateService({ current: '1.8.0', platform: 'win32', arch: 'x64',
    fetchImpl: async () => { calls++; return { ok: status === 200, status, json: async () => result }; },
    openExternal: async url => { opened = url; },
  });
  await assert.rejects(service.download(), /先检查更新/);
  const [first, second] = await Promise.all([service.check(), service.check()]);
  assert.equal(calls, 1);
  assert.deepEqual(first, second);
  assert.equal(first.hasUpdate, true);
  await service.download();
  assert.equal(opened, asset().browser_download_url);
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
