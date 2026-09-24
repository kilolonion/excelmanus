import { cpSync, mkdirSync, readdirSync, rmSync } from 'node:fs';
import { join } from 'node:path';

export function browserPayloadDirectories(cache) {
  // Old cache entries and .links must never become installed resources.
  const entries = readdirSync(cache, { withFileTypes: true })
    .filter(entry => entry.isDirectory() && /^(chromium_headless_shell|ffmpeg|winldd)-\d+$/.test(entry.name))
    .map(entry => entry.name).sort();
  for (const kind of ['chromium_headless_shell', 'ffmpeg', 'winldd']) {
    const count = entries.filter(name => name.startsWith(`${kind}-`)).length;
    if (count > 1 || (kind === 'chromium_headless_shell' && count !== 1)) {
      throw new Error(`Expected one current ${kind} payload; rebuild ${cache}`);
    }
  }
  return entries;
}

export function stageBrowser(cache, destination) {
  const entries = browserPayloadDirectories(cache);
  rmSync(destination, { recursive: true, force: true });
  mkdirSync(destination, { recursive: true });
  for (const name of entries) {
    cpSync(join(cache, name), join(destination, name), { recursive: true, verbatimSymlinks: true });
  }
  return entries;
}
