const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const { EventEmitter } = require('node:events');

test('firewall helper requests UAC and scopes the rule to this executable, private profile and local subnet', async () => {
  let call;
  const scope = { module: { exports: {} }, Buffer, process: { platform: 'win32', execPath: "C:\\Apps\\O'Brien\\ExcelManus.exe" }, require: name => {
    assert.equal(name, 'node:child_process');
    return { spawn: (...args) => { call = args; const child = new EventEmitter(); queueMicrotask(() => child.emit('exit', 0)); return child; } };
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../src/mobile-network.js'), 'utf8'), scope);
  assert.throws(() => scope.module.exports.allowPrivateNetwork('8787; bogus'));
  await scope.module.exports.allowPrivateNetwork(8787);
  assert.equal(call[2].windowsHide, true);
  const launcher = Buffer.from(call[1].at(-1), 'base64').toString('utf16le');
  assert.match(launcher, /-Verb RunAs/);
  const encoded = launcher.match(/'-EncodedCommand','([^']+)'/)[1];
  const script = Buffer.from(encoded, 'base64').toString('utf16le');
  assert.match(script, /-Profile Private -RemoteAddress LocalSubnet/);
  assert.match(script, /-LocalPort 8787/);
  assert.match(script, /O''Brien/);
  assert.ok(!script.includes('Set-NetFirewallProfile'));
});
