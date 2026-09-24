const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync, unlinkSync, symlinkSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { dirname, join, resolve } = require('node:path');
const { spawnSync } = require('node:child_process');

const desktopRoot = resolve(__dirname, '..');
const { build } = require('../package.json');

test('Windows assisted installer uses the activity UI and Unicode-safe 7z extraction', () => {
  assert.notEqual(build.nsis.useZip, true);
  assert.equal(build.nsis.oneClick, false);
  assert.equal(build.nsis.include, 'installer/progress.nsh');
});

test('native installer activity survives progress resets and restores success/failure UI', {
  skip: process.platform !== 'win32',
  timeout: 120_000,
}, async () => {
  // Use the same compiler as the pinned electron-builder. The fixture never
  // installs the app, touches its registry entries, or creates any shortcuts.
  const { getMakeNsisPath, getNsisPluginsPath } = require('app-builder-lib/out/toolsets/windows');
  const { archive } = require('app-builder-lib/out/targets/archive');
  const compiler = await getMakeNsisPath(build.toolsets?.nsis);
  const plugins = await getNsisPluginsPath(build.toolsets?.nsis);
  const tempRoot = resolve(tmpdir());
  const scratch = mkdtempSync(join(tempRoot, 'excelmanus-progress-'));
  const exe = join(scratch, 'progress.exe');
  const result = join(scratch, 'result.txt');
  const payload = join(scratch, 'payload');
  const installed = join(scratch, '安装 目录');
  const filename = join('运行环境 中文', '文件.txt');
  const manifest = join(scratch, 'expected-files.txt');
  const run = (file, args, env, expectedStatus = 0) => {
    const child = spawnSync(file, args, {
      encoding: 'utf8', windowsHide: true, timeout: 30_000,
      env: { ...process.env, ...env },
    });
    assert.ifError(child.error);
    assert.equal(child.status, expectedStatus, `${file} ${args.join(' ')}\n${child.stdout}\n${child.stderr}`);
  };
  try {
    // Match production's archive filter even when bypassing afterPack in this
    // isolated extraction fixture.
    const previousFilter = process.env.ELECTRON_BUILDER_7Z_FILTER;
    mkdirSync(join(payload, '运行环境 中文'), { recursive: true });
    writeFileSync(join(payload, filename), 'Unicode installer payload');
    const nestedFile = join('运行环境 中文', '保留父目录', '全新子目录', 'library.js');
    mkdirSync(dirname(join(payload, nestedFile)), { recursive: true });
    writeFileSync(join(payload, nestedFile), 'nested payload');
    const expectedFiles = [filename, nestedFile].map(file => file.replaceAll('/', '\\'));
    const writeManifest = files => writeFileSync(manifest, '\ufeff' + files.join('\r\n') + '\r\n', 'utf16le');
    writeManifest(expectedFiles);
    try {
      process.env.ELECTRON_BUILDER_7Z_FILTER = 'BCJ';
      await archive('7z', join(scratch, 'payload.7z'), payload, { withoutDir: true, compression: 'normal' });
    } finally {
      if (previousFilter === undefined) delete process.env.ELECTRON_BUILDER_7Z_FILTER;
      else process.env.ELECTRON_BUILDER_7Z_FILTER = previousFilter;
    }
    run(compiler.path, [
      '-WX', '-INPUTCHARSET', 'UTF8',
      `-DPROJECT_DIR=${desktopRoot}`,
      `-DPROGRESS_INCLUDE=${join(desktopRoot, build.nsis.include)}`,
      `-DTEST_EXE=${exe}`, `-DTEST_RESULT=${result}`,
      `-DTEST_PAYLOAD=${join(scratch, 'payload.7z')}`,
      `-DTEST_MANIFEST=${manifest}`,
      `-DTEST_INSTALL_DIR=${installed}`,
      `-DTEST_PLUGINS=${join(plugins, 'x86-unicode')}`,
      `-DNSIS_TEMPLATES=${resolve(require.resolve('app-builder-lib/package.json'), '..', 'templates', 'nsis')}`,
      join(__dirname, 'fixtures', 'installer-progress.nsi'),
    ], compiler.env);
    for (const language of ['1033', '2052', '1028']) {
      for (const mode of ['success', 'abort', 'silent']) {
        const args = mode === 'silent' ? ['/S', language] : [language, mode];
        run(exe, args, {}, mode === 'abort' ? 2 : 0);
        assert.equal(readFileSync(result, 'utf8').trim(), mode);
        assert.equal(readFileSync(join(installed, filename), 'utf8'), 'Unicode installer payload');
        assert.equal(readFileSync(join(installed, nestedFile), 'utf8'), 'nested payload');
        assert.ok(!readdirSync(installed).some(name => /^ns.*\.tmp$/i.test(name)), 'temporary extraction directory removed');
        // Repeated runs take the fallback path into an existing tree. Neither
        // that path nor a clean install may remove unrelated user files.
        const sentinel = join(installed, 'keep.txt');
        if (language === '1033' && mode === 'success') writeFileSync(sentinel, 'preserve');
        else assert.equal(readFileSync(sentinel, 'utf8'), 'preserve');
      }
    }
    // Simulate manifest removal leaving user data in a deeply shared parent.
    const preserved = join(installed, '运行环境 中文', '保留父目录', '用户.xlsx');
    writeFileSync(preserved, 'user data');
    unlinkSync(join(installed, filename));
    rmSync(dirname(join(installed, nestedFile)), { recursive: true });
    run(exe, ['/S', '1033'], {});
    assert.equal(readFileSync(preserved, 'utf8'), 'user data');
    assert.equal(readFileSync(join(installed, nestedFile), 'utf8'), 'nested payload');
    assert.equal(readFileSync(`${result}.copies`, 'utf8'), '0', 'shared parents must not trigger whole-tree copying');
    const outside = join(scratch, 'outside-workspace');
    mkdirSync(outside);
    writeFileSync(join(outside, 'library.js'), 'external user data');
    rmSync(dirname(join(installed, nestedFile)), { recursive: true });
    symlinkSync(outside, dirname(join(installed, nestedFile)), 'junction');
    run(exe, ['/S', '1033'], {}, 2);
    assert.equal(readFileSync(join(outside, 'library.js'), 'utf8'), 'external user data', 'merge must not traverse a junction');
    unlinkSync(dirname(join(installed, nestedFile)));
    // A decoder returning after skipping an EXE block must fail against the
    // trusted manifest before any existing program file is replaced.
    writeManifest([...expectedFiles, 'missing-native.dll']);
    writeFileSync(join(installed, filename), 'must remain');
    run(exe, ['/S', '1033'], {}, 2);
    assert.equal(readFileSync(join(installed, filename), 'utf8'), 'must remain');
  } finally {
    assert.equal(dirname(scratch), tempRoot);
    rmSync(scratch, { recursive: true, force: true });
  }
});
