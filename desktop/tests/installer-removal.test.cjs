const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { collectFiles } = require('../scripts/installer-manifest.cjs');

test('native removal preserves user files and rejects traversal and junctions', { skip: process.platform !== 'win32', timeout: 120_000 }, async () => {
  const { getMakeNsisPath } = require('app-builder-lib/out/toolsets/windows');
  const compiler = await getMakeNsisPath();
  const scratch = fs.mkdtempSync(path.join(tmpdir(), 'excelmanus-removal-'));
  const installed = path.join(scratch, '安装 目录');
  const manifest = path.join(scratch, 'files.txt');
  const exe = path.join(scratch, 'remove.exe');
  const run = (file, args, env) => {
    const child = spawnSync(file, args, { encoding: 'utf8', windowsHide: true, timeout: 30_000, env: { ...process.env, ...env } });
    assert.ifError(child.error);
    return child;
  };
  const writeManifest = lines => fs.writeFileSync(manifest, '\ufeff' + lines.join('\r\n') + '\r\n', 'utf16le');
  try {
    fs.mkdirSync(path.join(installed, 'resources', '中文'), { recursive: true });
    fs.writeFileSync(path.join(installed, 'ExcelManus.exe'), 'program');
    fs.writeFileSync(path.join(installed, 'resources', '中文', 'module.js'), 'program module');
    const nested = path.join(installed, 'resources', 'empty-after-removal', 'nested');
    fs.mkdirSync(nested, { recursive: true });
    for (let i = 0; i < 16; i++) fs.writeFileSync(path.join(nested, `${i}.js`), 'program');
    const owned = await collectFiles(installed);
    const userFile = path.join(installed, 'resources', '中文', '我的表格.xlsx');
    fs.writeFileSync(userFile, 'user workbook');
    fs.mkdirSync(path.join(installed, 'profile', 'data'), { recursive: true });
    const workspace = path.join(installed, 'profile', 'data', 'workspace.xlsx');
    fs.writeFileSync(workspace, 'workspace');
    const compiled = run(compiler.path, ['-WX', '-INPUTCHARSET', 'UTF8', `-DPROJECT_DIR=${path.resolve(__dirname, '..')}`, `-DTEST_EXE=${exe}`, `-DTEST_INSTALL_DIR=${installed}`, `-DTEST_MANIFEST=${manifest}`, path.join(__dirname, 'fixtures', 'installer-removal.nsi')], compiler.env);
    assert.equal(compiled.status, 0, compiled.stdout + compiled.stderr);
    writeManifest(['missing\\new.js']);
    assert.equal(run(exe, []).status, 0, 'nonexistent destinations are valid during a first install');
    for (const invalid of ['..\\outside.txt', '\\absolute.txt', 'C:\\absolute.txt', 'nested/escape.js', 'nested\\*.js', 'nested\\?.js', 'file.txt:stream', 'nested\\..\\outside.txt']) {
      writeManifest([...owned, invalid]);
      assert.equal(run(exe, []).status, 2, invalid);
      assert.equal(fs.readFileSync(path.join(installed, 'ExcelManus.exe'), 'utf8'), 'program', 'validation must complete before deletion');
    }
    // Also accept LF and a final line without a newline (legacy manifests).
    fs.writeFileSync(manifest, '\ufeff' + owned.join('\n'), 'utf16le');
    assert.equal(run(exe, []).status, 0);
    assert.equal(fs.existsSync(path.join(installed, 'ExcelManus.exe')), false);
    assert.equal(fs.existsSync(path.dirname(nested)), false, 'last sibling removes the empty directory chain');
    assert.equal(fs.readFileSync(userFile, 'utf8'), 'user workbook');
    assert.equal(fs.readFileSync(workspace, 'utf8'), 'workspace');
    const outside = path.join(scratch, 'outside');
    fs.mkdirSync(outside);
    fs.writeFileSync(path.join(outside, 'keep.txt'), 'outside');
    fs.symlinkSync(outside, path.join(installed, 'linked'), 'junction');
    writeManifest(['linked\\keep.txt']);
    assert.equal(run(exe, []).status, 2);
    assert.equal(fs.readFileSync(path.join(outside, 'keep.txt'), 'utf8'), 'outside');
  } finally {
    assert.equal(path.dirname(scratch), path.resolve(tmpdir()));
    fs.rmSync(scratch, { recursive: true, force: true });
  }
});
