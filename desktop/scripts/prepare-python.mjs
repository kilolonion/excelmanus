// Bundle a real relocatable interpreter: the PyInstaller sidecar is not Python.
import { cpSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import { join, resolve, relative, isAbsolute, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const project = resolve(root, '..');
const run = (cmd, args, options = {}) => execFileSync(cmd, args, { cwd: project, stdio: 'inherit', ...options });
// uv's managed CPython is python-build-standalone, unlike a system/Homebrew Python.
const sourcePython = run('uv', ['python', 'find', '--managed-python', '--system', '3.12'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
if (process.platform === 'win32' && process.arch !== 'x64') throw new Error('Windows packaging currently supports x64 only; do not mix ARM64 and x64 runtimes');
const pythonArch = run(sourcePython, ['-I', '-B', '-X', 'utf8', '-c', 'import platform; print(platform.machine().lower())'], {encoding:'utf8',stdio:['ignore','pipe','inherit']}).trim();
const normalizeArch = value => ({amd64:'x64',x86_64:'x64',aarch64:'arm64'}[value] || value);
if (normalizeArch(pythonArch) !== process.arch) throw new Error(`Python architecture ${pythonArch} does not match Node ${process.arch}`);
const prefix = run(sourcePython, ['-I', '-B', '-X', 'utf8', '-c', 'import sys; print(sys.base_prefix)'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
const target = join(root, '.build', 'runtime', 'python');
rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
cpSync(prefix, target, { recursive: true, verbatimSymlinks: true });
const python = join(target, process.platform === 'win32' ? 'python.exe' : 'bin/python3');
if (!existsSync(python)) throw new Error(`Managed Python layout not supported: ${prefix}`);
const site = run(python, ['-I', '-B', '-X', 'utf8', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] }).trim();
const siteRelative = relative(target, site);
if (siteRelative === '..' || siteRelative.startsWith(`..${sep}`) || isAbsolute(siteRelative)) throw new Error('Python is not relocatable');
const requirements = join(root, '.build', 'python-requirements.txt');
run('uv', ['export', '--frozen', '--no-dev', '--no-emit-project', '--extra', 'analysis', '--extra', 'vba', '--extra', 'web', '-o', requirements]);
run('uv', ['pip', 'install', '--python', python, '--target', site, '--requirements', requirements]);
run(python, ['-I', '-B', '-X', 'utf8', '-c', 'import pandas, openpyxl, xlrd, pyxlsb, xlsxwriter, matplotlib, scipy, sklearn, seaborn, plotly; print("Bundled Python imports OK")']);
