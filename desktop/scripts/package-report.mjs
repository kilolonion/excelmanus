// Measure the bytes users download/install; compare snapshots without relying
// on build logs or rounded directory sizes. No writes to the package itself.
import { readdirSync, statSync, readFileSync, writeFileSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const args = process.argv.slice(2);
const root = resolve(args.shift() || 'dist/win-unpacked');
const option = name => { const i = args.indexOf(name); return i < 0 ? null : args[i + 1]; };
const components = {};
let bytes = 0, files = 0;
function walk(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const file = join(directory, entry.name);
    if (entry.isDirectory()) walk(file);
    else if (entry.isFile()) {
      const size = statSync(file).size;
      const parts = relative(root, file).replaceAll('\\', '/').split('/');
      const index = parts[0] === 'resources' ? 0
        : parts[0] === 'Contents' && parts[1] === 'Resources' ? 1 : -1;
      const component = index >= 0 ? parts[index + 1] : 'shell';
      const group = components[component] ||= { bytes: 0, files: 0 };
      group.bytes += size;
      group.files++;
      bytes += size;
      files++;
    }
  }
}
walk(root);
const report = { root, bytes, files, components };
const installer = option('--installer');
if (installer) report.installer = { path: resolve(installer), bytes: statSync(installer).size };
const baseline = option('--baseline');
if (baseline) {
  const previous = JSON.parse(readFileSync(baseline, 'utf8'));
  report.change = { bytes: bytes - previous.bytes, files: files - previous.files };
  if (report.installer && previous.installer) report.change.installerBytes = report.installer.bytes - previous.installer.bytes;
}
const json = JSON.stringify(report, null, 2) + '\n';
const output = option('--output');
if (output) writeFileSync(output, json);
process.stdout.write(json);
