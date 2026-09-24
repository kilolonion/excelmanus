const { test } = require('node:test');
const assert = require('node:assert/strict');

test('runtime cache rejects the right Node version for the wrong OS or architecture', async () => {
  const { assertNodeIdentity, bundledNodeVersion } = await import('../scripts/node-runtime.mjs');
  const expected = { version: bundledNodeVersion, platform: 'win32', arch: 'x64' };
  assert.deepEqual(assertNodeIdentity(expected, expected), expected);
  for (const changed of [{ arch: 'arm64' }, { platform: 'darwin' }, { version: 'v20.0.0' }]) {
    assert.throws(() => assertNodeIdentity({ ...expected, ...changed }, expected), /Bundled Node/);
  }
  assert.throws(() => assertNodeIdentity(null, expected), /missing/);
});
