const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { tmpdir } = require('node:os');

test('installed acceptance detects missing, truncated and same-size corrupt native files', async () => {
  const { verifyInstalled } = await import('../scripts/verify-installed.mjs');
  const work = await fs.mkdtemp(path.join(tmpdir(), 'excelmanus-verify-'));
  try {
    const reference = path.join(work, 'source'), installed = path.join(work, 'installed');
    await fs.mkdir(path.join(reference, 'resources'), { recursive: true });
    await fs.writeFile(path.join(reference, 'resources', '原生.dll'), 'original');
    await fs.writeFile(path.join(reference, 'ExcelManus.exe'), 'app');
    await fs.cp(reference, installed, { recursive: true });
    await fs.writeFile(path.join(installed, '用户.xlsx'), 'user data');
    assert.deepEqual(await verifyInstalled(reference, installed), { files: 2, bytes: 11 });
    for (const content of ['truncated payload', 'modified']) {
      await fs.writeFile(path.join(installed, 'resources', '原生.dll'), content);
      await assert.rejects(verifyInstalled(reference, installed), /differs/);
    }
    await fs.unlink(path.join(installed, 'resources', '原生.dll'));
    await assert.rejects(verifyInstalled(reference, installed), /ENOENT/);
  } finally { await fs.rm(work, { recursive: true, force: true }); }
});
