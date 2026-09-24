const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { tmpdir } = require('node:os');
const { randomUUID } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const afterPack = require('../scripts/installer-manifest.cjs');
const { MANIFEST } = afterPack;

test('real NSIS templates replace legacy/current installs and preserve workspaces in both modes', {
  skip: process.platform !== 'win32', timeout: 180_000,
}, async () => {
  const { build, Platform, Arch } = require('electron-builder');
  const root = fs.mkdtempSync(path.join(tmpdir(), 'excelmanus-upgrade-'));
  const guid = randomUUID();
  const product = `ExcelManus Fixture ${guid.slice(0, 8)}`;
  const installed = path.join(root, '安装 目录');
  const other = path.join(root, 'another-version');
  const payload = path.join(root, 'payload');
  const output = path.join(root, 'dist');
  const executable = `${product}.exe`;
  const reg = path.join(process.env.SystemRoot, 'System32', 'reg.exe');
  const installer = path.join(output, 'fixture.exe');
  const uninstaller = path.join(installed, `Uninstall ${product}.exe`);
  const registration = `HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\${guid}`;
  const run = (file, args, expect = 0) => {
    const child = spawnSync(file, args, { encoding: 'utf8', windowsHide: true, windowsVerbatimArguments: true, timeout: 40_000 });
    assert.ifError(child.error);
    assert.equal(child.status, expect, `${file}\n${child.stdout}\n${child.stderr}`);
    return child.stdout;
  };
  let registered = false;
  try {
    fs.mkdirSync(payload);
    fs.mkdirSync(path.join(payload, 'resources'));
    fs.mkdirSync(path.join(root, '.build'));
    fs.cpSync(path.resolve(__dirname, '../installer'), path.join(root, 'installer'), { recursive: true });
    fs.symlinkSync(path.resolve(__dirname, '../node_modules'), path.join(root, 'node_modules'), 'junction');
    fs.writeFileSync(path.join(payload, executable), 'fixture program');
    fs.writeFileSync(path.join(payload, 'resources', 'owned.js'), 'new code');
    fs.mkdirSync(path.join(payload, 'new-assets'));
    fs.writeFileSync(path.join(payload, 'new-assets', 'new.js'), 'new module');
    await afterPack({ electronPlatformName: 'win32', appOutDir: payload, packager: { projectDir: root, appInfo: { productFilename: product } } });
    const manifest = fs.readFileSync(path.join(payload, MANIFEST), 'utf16le');
    fs.writeFileSync(path.join(root, 'package.json'), JSON.stringify({ name: `excelmanus-fixture-${guid}`, version: '1.9.0', description: 'Isolated installer acceptance fixture', author: 'ExcelManus', main: 'index.js' }));
    await build({ projectDir: root, prepackaged: payload, targets: Platform.WINDOWS.createTarget('nsis', Arch.x64), publish: 'never', config: {
      appId: `com.excelmanus.fixture.${guid}`, productName: product,
      electronVersion: require('../package.json').devDependencies.electron,
      directories: { output }, compression: 'store',
      win: { signAndEditExecutable: false },
      nsis: { guid, include: 'installer/progress.nsh', artifactName: 'fixture.exe', oneClick: false, perMachine: false,
        allowToChangeInstallationDirectory: false, createDesktopShortcut: false, createStartMenuShortcut: false, runAfterFinish: false, deleteAppDataOnUninstall: false },
    } });
    // NSIS /D and _? consume the unquoted remainder of the command line.
    run(installer, ['/S', '/currentuser', `/D=${installed}`]);
    registered = true;
    assert.ok(fs.existsSync(path.join(installed, executable)));
    const installerCache = path.join(process.env.LOCALAPPDATA, `excelmanus-fixture-${guid}-updater`, 'installer.exe');
    assert.equal(fs.existsSync(installerCache), false, 'full updates need no duplicate electron-updater cache');
    fs.mkdirSync(path.join(installed, 'profile', 'data'), { recursive: true });
    const workspace = path.join(installed, 'profile', 'data', '预算.xlsx');
    const settings = path.join(installed, 'profile', 'excelmanus.db');
    const userFile = path.join(installed, 'resources', '用户文档.docx');
    fs.writeFileSync(workspace, 'workspace bytes');
    fs.writeFileSync(settings, 'settings and conversations');
    fs.writeFileSync(userFile, 'document bytes');
    const assertPreserved = () => {
      assert.equal(fs.readFileSync(workspace, 'utf8'), 'workspace bytes');
      assert.equal(fs.readFileSync(settings, 'utf8'), 'settings and conversations');
      assert.equal(fs.readFileSync(userFile, 'utf8'), 'document bytes');
    };
    // Emulate a pre-manifest release with an unusable/unsafe old uninstaller.
    // The new installer must use its own uninstaller instead of launching it.
    fs.unlinkSync(path.join(installed, MANIFEST));
    fs.writeFileSync(uninstaller, 'DO NOT EXECUTE A LEGACY UNINSTALLER');
    run(reg, ['add', registration, '/v', 'DisplayVersion', '/t', 'REG_SZ', '/d', '1.0.0', '/f']);
    for (const mode of ['migrate', 'reinstall']) {
      fs.writeFileSync(path.join(installed, 'resources', 'owned.js'), 'old code');
      // Even explicit scope changes must not produce a per-machine duplicate.
      run(installer, ['/S', mode === 'reinstall' ? '/allusers' : '/currentuser', `/excelmanus-mode=${mode}`, `/D=${other}`]);
      assert.equal(fs.readFileSync(path.join(installed, 'resources', 'owned.js'), 'utf8'), 'new code');
      assert.equal(fs.existsSync(path.join(other, executable)), false, 'changing destination must not create a second app');
      assert.match(run(reg, ['query', registration, '/v', 'DisplayVersion']), /1\.9\.0/);
      assertPreserved();
    }
    // A new payload path (absent from an older manifest) must not write through
    // an existing directory junction into user-owned data outside the install.
    const outside = path.join(root, 'outside-workspace');
    fs.mkdirSync(outside);
    fs.writeFileSync(path.join(outside, 'new.js'), 'user file');
    fs.unlinkSync(path.join(installed, 'new-assets', 'new.js'));
    fs.rmdirSync(path.join(installed, 'new-assets'));
    fs.symlinkSync(outside, path.join(installed, 'new-assets'), 'junction');
    fs.writeFileSync(path.join(installed, MANIFEST), manifest.replace('new-assets\\new.js\r\n', ''), 'utf16le');
    run(installer, ['/S', '/currentuser'], 2);
    assert.equal(fs.readFileSync(path.join(outside, 'new.js'), 'utf8'), 'user file');
    assert.ok(fs.existsSync(path.join(installed, executable)));
    assertPreserved();
    fs.unlinkSync(path.join(installed, 'new-assets'));
    fs.writeFileSync(path.join(installed, MANIFEST), manifest + '..\\outside.txt\r\n', 'utf16le');
    fs.writeFileSync(path.join(installed, 'resources', 'owned.js'), 'must remain if cleanup fails');
    run(installer, ['/S', '/currentuser'], 2);
    assert.equal(fs.readFileSync(path.join(installed, 'resources', 'owned.js'), 'utf8'), 'must remain if cleanup fails');
    assertPreserved();
    fs.writeFileSync(path.join(installed, MANIFEST), manifest, 'utf16le');
    run(uninstaller, ['/S', '--delete-app-data', `/currentuser`, `_?=${installed}`], 2);
    assertPreserved();
    run(uninstaller, ['/S', '/currentuser', `_?=${installed}`]);
    registered = false;
    assert.equal(fs.existsSync(path.join(installed, executable)), false);
    assertPreserved();
    run(reg, ['query', registration], 1);
  } catch (error) {
    console.error('Installer acceptance failure:', error);
    throw error;
  } finally {
    if (registered && fs.existsSync(uninstaller)) {
      // Best effort cleanup of this test's unique registration, never real app IDs.
      spawnSync(uninstaller, ['/S', '/currentuser', `_?=${installed}`], { windowsHide: true, windowsVerbatimArguments: true, timeout: 30_000 });
    }
    for (const key of [registration, `HKCU\\Software\\${guid}`]) {
      spawnSync(reg, ['delete', key, '/f'], { windowsHide: true });
    }
    assert.equal(path.dirname(root), path.resolve(tmpdir()));
    fs.rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
    const cache = path.resolve(process.env.LOCALAPPDATA, `excelmanus-fixture-${guid}-updater`);
    assert.equal(path.dirname(cache), path.resolve(process.env.LOCALAPPDATA));
    fs.rmSync(cache, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
  }
});
