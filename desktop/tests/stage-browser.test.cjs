const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { tmpdir } = require('node:os');

test('browser staging excludes headed/stale payloads and preserves the headless executable bytes', async () => {
  const { stageBrowser } = await import('../scripts/stage-browser.mjs');
  const work = fs.mkdtempSync(path.join(tmpdir(), 'excelmanus-browser-'));
  try {
    const cache = path.join(work, 'cache'), output = path.join(work, 'output');
    for (const directory of ['chromium_headless_shell-1234', 'ffmpeg-1000', 'chromium-1234', '.links', 'firefox-9999']) {
      fs.mkdirSync(path.join(cache, directory), { recursive: true });
      fs.writeFileSync(path.join(cache, directory, 'executable'), directory);
    }
    assert.deepEqual(stageBrowser(cache, output), ['chromium_headless_shell-1234', 'ffmpeg-1000']);
    assert.deepEqual(fs.readdirSync(output).sort(), ['chromium_headless_shell-1234', 'ffmpeg-1000']);
    assert.equal(fs.readFileSync(path.join(output, 'chromium_headless_shell-1234/executable'), 'utf8'), 'chromium_headless_shell-1234');
    fs.mkdirSync(path.join(cache, 'chromium_headless_shell-1233'));
    assert.throws(() => stageBrowser(cache, output), /Expected one current/);
    assert.ok(fs.existsSync(path.join(output, 'chromium_headless_shell-1234/executable')), 'validate before replacing payload');
  } finally { fs.rmSync(work, { recursive: true, force: true }); }
});
