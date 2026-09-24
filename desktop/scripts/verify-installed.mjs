// Acceptance-only byte comparison, outside the measured installer process.
// A successful exit or working main EXE cannot prove all native/data files made
// it through the actual NSIS decoder. User-created extra files are allowed.
import { createHash } from 'node:crypto';
import { createReadStream } from 'node:fs';
import { lstat, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

async function digest(file) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest('hex');
}

export async function verifyInstalled(reference, installed) {
  const total = { files: 0, bytes: 0 };
  async function walk(relative = '') {
    const source = join(reference, relative), target = join(installed, relative);
    const info = await lstat(target);
    if (info.isSymbolicLink() || !info.isDirectory()) throw new Error(`Invalid installed directory: ${target}`);
    for (const entry of await readdir(source, { withFileTypes: true })) {
      const name = join(relative, entry.name);
      if (entry.isSymbolicLink()) throw new Error(`Unexpected payload link: ${name}`);
      if (entry.isDirectory()) { await walk(name); continue; }
      if (!entry.isFile()) throw new Error(`Unexpected payload entry: ${name}`);
      const from = join(reference, name), to = join(installed, name);
      const [expected, actual] = await Promise.all([lstat(from), lstat(to)]);
      if (!actual.isFile() || actual.size !== expected.size || await digest(from) !== await digest(to)) {
        throw new Error(`Installed payload differs: ${name}`);
      }
      total.files++;
      total.bytes += expected.size;
    }
  }
  await walk();
  return total;
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  if (process.argv.length !== 4) throw new Error('Usage: verify-installed.mjs <win-unpacked> <installed>');
  console.log('INSTALLED_PAYLOAD_OK', await verifyInstalled(resolve(process.argv[2]), resolve(process.argv[3])));
}
