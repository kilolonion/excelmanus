const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { tmpdir } = require('node:os');

test('publishing rejects incomplete releases and restores the previous release after a locked rename', async () => {
  const { publishRelease } = await import('../../scripts/release-output.mjs');
  const work = fs.mkdtempSync(path.join(tmpdir(), 'excelmanus-release-'));
  const staging = path.join(work, 'staging'), destination = path.join(work, 'release');
  try {
    fs.mkdirSync(staging); fs.mkdirSync(destination);
    fs.writeFileSync(path.join(destination, 'installer.exe'), 'previous-good-build');
    assert.throws(() => publishRelease(staging, destination), /ENOENT/);
    assert.equal(fs.readFileSync(path.join(destination, 'installer.exe'), 'utf8'), 'previous-good-build');
    fs.writeFileSync(path.join(staging, 'release-manifest.json'), '{}');
    fs.writeFileSync(path.join(staging, 'SHA256SUMS.txt'), 'hash');
    fs.writeFileSync(path.join(staging, 'installer.exe'), 'new-build');
    assert.throws(() => publishRelease(staging, destination, {
      renameSync(from, to) {
        if (from === staging) throw Object.assign(new Error('locked'), { code: 'EPERM' });
        fs.renameSync(from, to);
      }, rmSync: fs.rmSync,
    }), /locked/);
    assert.equal(fs.readFileSync(path.join(destination, 'installer.exe'), 'utf8'), 'previous-good-build');
    assert.ok(fs.existsSync(path.join(staging, 'installer.exe')));
    assert.deepEqual(publishRelease(staging, destination), { retainedBackup: null });
    assert.equal(fs.readFileSync(path.join(destination, 'installer.exe'), 'utf8'), 'new-build');
    assert.deepEqual(fs.readdirSync(work), ['release']);
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});

test('failed rollback preserves the old deliverable at the reported backup path', async () => {
  const { publishRelease } = await import('../../scripts/release-output.mjs');
  const work = fs.mkdtempSync(path.join(tmpdir(), 'excelmanus-rollback-'));
  const staging = path.join(work, 'staging'), destination = path.join(work, 'release');
  try {
    fs.mkdirSync(staging); fs.mkdirSync(destination);
    fs.writeFileSync(path.join(destination, 'installer.exe'), 'previous');
    for (const file of ['release-manifest.json', 'SHA256SUMS.txt']) fs.writeFileSync(path.join(staging, file), 'metadata');
    let count = 0;
    assert.throws(() => publishRelease(staging, destination, {
      renameSync(from, to) {
        if (++count > 1) throw new Error('locked');
        fs.renameSync(from, to);
      }, rmSync: fs.rmSync,
    }), /previous release is preserved at/);
    const backup = fs.readdirSync(work).find(name => name.startsWith('.release-backup-'));
    assert.equal(fs.readFileSync(path.join(work, backup, 'previous/installer.exe'), 'utf8'), 'previous');
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});
