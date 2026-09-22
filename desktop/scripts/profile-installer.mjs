// Benchmark the real NSIS extraction/copy operations without registering or
// replacing an installed application. All output stays in a fresh build folder.
import { createReadStream, createWriteStream, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { pipeline } from 'node:stream/promises';
import { createRequire } from 'node:module';
import { spawn, execFileSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

if (process.platform !== 'win32') throw new Error('Run the installer profiler on Windows');
const require = createRequire(import.meta.url);
const { getPath7za } = require('app-builder-lib/out/toolsets/7zip');
const { getMakeNsisPath, getNsisPluginsPath } = require('app-builder-lib/out/toolsets/windows');
const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const installer = resolve(args.find(arg => !arg.startsWith('--')) || join(desktopRoot, 'dist', 'ExcelManus Setup 1.8.0.exe'));
const fast = args.includes('--fast');
const ownedRemoval = args.includes('--owned-removal');
const work = join(desktopRoot, '.build', 'install-profile', `run-${Date.now()}`);
mkdirSync(work, { recursive: true });
const listing = execFileSync(await getPath7za(), ['l', '-slt', installer], { encoding: 'utf8', maxBuffer: 16 * 1024 * 1024 });
const offset = Number(listing.match(/^Offset = (\d+)$/m)?.[1]);
const size = Number(listing.match(/^Physical Size = (\d+)$/m)?.[1]);
if (!Number.isSafeInteger(offset) || !Number.isSafeInteger(size) || size <= 0) throw new Error('Embedded 7z payload not found');
const archive = join(work, 'payload.7z');
await pipeline(createReadStream(installer, { start: offset, end: offset + size - 1 }), createWriteStream(archive));
const compiler = await getMakeNsisPath();
const plugins = await getNsisPluginsPath();
const result = join(work, 'timings.jsonl');
const exe = join(work, 'profile.exe');
execFileSync(compiler.path, [
  '-WX', '-INPUTCHARSET', 'UTF8', `-DBENCH_EXE=${exe}`,
  `-DBENCH_ROOT=${work}`, `-DBENCH_ARCHIVE=${archive}`,
  `-DBENCH_RESULT=${result}`, `-DBENCH_PLUGINS=${join(plugins, 'x86-unicode')}`,
  `-DPROJECT_DIR=${desktopRoot}`, ...(fast ? ['-DBENCH_FAST'] : []),
  ...(ownedRemoval ? ['-DBENCH_OWNED_REMOVAL'] : []),
  join(desktopRoot, 'tests', 'fixtures', 'installer-profile.nsi'),
], { encoding: 'utf8', env: { ...process.env, ...compiler.env } });
console.log(`Profile output: ${result}`);
const started = performance.now();
let printed = '';
const printUpdates = () => {
  try {
    const text = readFileSync(result, 'utf8');
    if (text !== printed) { process.stdout.write(text.slice(printed.length)); printed = text; }
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
};
const timer = setInterval(printUpdates, 1000);
let failure;
try {
  const child = spawn(exe, ['/S'], { windowsHide: true, stdio: 'inherit' });
  await new Promise((resolveRun, reject) => {
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolveRun() : reject(new Error(`Profile exited ${code}`)));
  });
} catch (error) { failure = error; }
finally { clearInterval(timer); printUpdates(); }
const summary = { installer, fast, ownedRemoval, completed: !failure, wallMs: Math.round(performance.now() - started), stages: printed.trim().split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line)) };
writeFileSync(join(work, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
if (failure) throw failure;
console.log(`Total process time: ${summary.wallMs} ms`);
