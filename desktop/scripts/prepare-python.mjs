// Bundle a real relocatable interpreter: the PyInstaller sidecar is not Python.
import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import { join, resolve, relative, isAbsolute, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { pythonBaseFilter, prunePythonRuntime } from './runtime-files.mjs';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const project = resolve(root, '..');
const homeDir = process.env.USERPROFILE || process.env.HOME || '';
const extraPaths = [
  join(homeDir, '.local', 'bin'),
  join(homeDir, '.cargo', 'bin'),
].filter(p => existsSync(p));
const envPath = [...extraPaths, process.env.PATH].filter(Boolean).join(process.platform === 'win32' ? ';' : ':');
const run = (cmd, args, options = {}) => execFileSync(cmd, args, { cwd: project, stdio: 'inherit', env: { ...process.env, PATH: envPath }, ...options });
// uv's managed CPython is python-build-standalone, unlike a system/Homebrew Python.
const sourcePython = run('uv', ['python', 'find', '--python-preference', 'only-managed', '--system', '3.12'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
if (process.platform === 'win32' && process.arch !== 'x64') throw new Error('Windows packaging currently supports x64 only; do not mix ARM64 and x64 runtimes');
const pythonArch = run(sourcePython, ['-I', '-B', '-X', 'utf8', '-c', 'import platform; print(platform.machine().lower())'], {encoding:'utf8',stdio:['ignore','pipe','inherit']}).trim();
const normalizeArch = value => ({amd64:'x64',x86_64:'x64',aarch64:'arm64'}[value] || value);
if (normalizeArch(pythonArch) !== process.arch) throw new Error(`Python architecture ${pythonArch} does not match Node ${process.arch}`);
const prefix = run(sourcePython, ['-I', '-B', '-X', 'utf8', '-c', 'import sys; print(sys.base_prefix)'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
// An optional output under .build lets validation run without replacing a
// runtime that another packaging job is using.
const buildRoot = join(root, '.build');
const target = resolve(process.argv[2] || join(buildRoot, 'runtime', 'python'));
const targetRelative = relative(buildRoot, target);
if (!targetRelative || targetRelative === '..' || targetRelative.startsWith(`..${sep}`) || isAbsolute(targetRelative)) throw new Error('Python output must be inside desktop/.build');
rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
cpSync(prefix, target, { recursive: true, verbatimSymlinks: true, filter: file => pythonBaseFilter(prefix, file) });
const python = join(target, process.platform === 'win32' ? 'python.exe' : 'bin/python3');
if (!existsSync(python)) throw new Error(`Managed Python layout not supported: ${prefix}`);
const site = run(python, ['-I', '-B', '-X', 'utf8', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
const siteRelative = relative(target, site);
if (siteRelative === '..' || siteRelative.startsWith(`..${sep}`) || isAbsolute(siteRelative)) throw new Error('Python is not relocatable');
const requirements = `${target}-requirements.txt`;
run('uv', ['export', '--frozen', '--only-group', 'desktop-runtime', '-o', requirements], { stdio: ['ignore', 'ignore', 'inherit'] });
run('uv', ['pip', 'install', '--python', python, '--target', site, '--requirements', requirements]);
console.log('Removed Python test/cache files:', prunePythonRuntime(target));
run(python, ['-I', '-B', '-X', 'utf8', join(root, 'scripts', 'check-python-runtime.py')]);
