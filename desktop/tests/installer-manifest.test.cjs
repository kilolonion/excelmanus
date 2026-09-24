const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { tmpdir } = require('node:os');
const afterPack = require('../scripts/installer-manifest.cjs');
const { compute7zCompressArgs } = require('app-builder-lib/out/targets/archive');

test('Windows packaging forces a decoder-compatible filter and records Unicode payload files', async () => {
  const work = await fs.mkdtemp(path.join(tmpdir(), 'excelmanus-manifest-'));
  const previous = process.env.ELECTRON_BUILDER_7Z_FILTER;
  try {
    const payload = path.join(work, 'payload');
    await fs.mkdir(path.join(payload, 'resources'), { recursive: true });
    await fs.writeFile(path.join(payload, 'ExcelManus.exe'), 'fixture');
    await fs.writeFile(path.join(payload, 'resources/数据.txt'), 'data');
    process.env.ELECTRON_BUILDER_7Z_FILTER = 'BCJ2';
    await afterPack({ electronPlatformName: 'win32', appOutDir: payload,
      packager: { projectDir: work, appInfo: { productFilename: 'ExcelManus' } } });
    assert.ok(compute7zCompressArgs('7z').includes('-mf=BCJ'));
    assert.ok(!compute7zCompressArgs('7z').includes('-mf=BCJ2'));
    const contents = await fs.readFile(path.join(payload, afterPack.MANIFEST), 'utf16le');
    assert.ok(contents.startsWith('\ufeff'));
    assert.match(contents, /resources\\数据.txt\r\n/);
    assert.match(contents, /resources\\elevate.exe\r\n/);
    assert.equal(contents, await fs.readFile(path.join(work, '.build', afterPack.MANIFEST), 'utf16le'));
    assert.ok(!contents.includes(afterPack.MANIFEST));
  } finally {
    if (previous === undefined) delete process.env.ELECTRON_BUILDER_7Z_FILTER;
    else process.env.ELECTRON_BUILDER_7Z_FILTER = previous;
    await fs.rm(work, { recursive: true, force: true });
  }
});
