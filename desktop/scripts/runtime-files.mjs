import { existsSync, readdirSync, rmSync, statSync } from 'node:fs';
import { isAbsolute, join, relative, resolve } from 'node:path';

const parts = (root, file) => relative(root, file).replaceAll('\\', '/').split('/');

// The packaged interpreter is headless and never creates virtualenvs or
// installs packages. Keep the standard library needed by application code,
// while leaving GUI/developer payloads out of the installer.
const unusedStdlib = new Set([
  '__phello__', 'idlelib', 'ensurepip', 'tkinter', 'turtledemo',
  'venv', 'lib2to3', 'pydoc_data',
]);
const unusedRuntimeRoots = new Set([
  'include', 'share', 'tcl8', 'tcl8.6', 'tk8', 'tk8.6', 'itcl4.2.4',
]);
const unusedBinFiles = new Set([
  '2to3', '2to3-3.12', 'idle3', 'idle3.12', 'pydoc3', 'pydoc3.12',
  'pip', 'pip3', 'pip3.12', 'python-config', 'python3-config',
  'python3.12-config',
]);

export function pythonBaseFilter(root, file) {
  const path = parts(root, file);
  const site = path.indexOf('site-packages');
  // Populate a clean site-packages from the locked runtime group, without the
  // managed interpreter's pip, setuptools or build-environment packages.
  if (site >= 0 && site < path.length - 1) return false;
  if (path[0]?.toLowerCase() === 'bin' && unusedBinFiles.has(path[1])) return false;
  if (path.some(part => unusedRuntimeRoots.has(part.toLowerCase()))) return false;
  const stdlib = site >= 0 ? path.slice(0, site) : path;
  return !stdlib.some(part => unusedStdlib.has(part.toLowerCase()))
    && !path.some(part => part === '__pycache__')
    && !path.some((part, i) => part === 'test' && /^(lib|python3\.\d+)$/i.test(path[i - 1] || ''));
}

export function frontendRuntimeFilter(root, file) {
  const path = parts(root, file);
  if (path.includes('.git') || path.includes('.DS_Store')) return false;
  if (path.some((part, i) => part === 'cache' && path[i - 1] === '.next')) return false;
  if (!path.includes('node_modules') && !path.includes('next_modules')) return true;
  return !path.some(part => ['test', 'tests', '__tests__'].includes(part))
    && !/\.(?:d\.[cm]?ts|map)$/.test(path.at(-1));
}

// Keep numpy.testing, pandas._testing, scipy._lib._testutils, datasets, fonts,
// native libraries and license metadata. Only remove known library test suites.
const testPackages = new Set([
  'numpy', 'pandas', 'scipy', 'sklearn', 'matplotlib', 'seaborn', 'joblib',
  'openpyxl', 'xlsxwriter', 'docx', 'pil', 'oletools', 'fonttools', 'lxml',
]);

export function prunePythonRuntime(directory) {
  const root = resolve(directory);
  const removed = { files: 0, bytes: 0 };
  function measure(file) {
    const stat = statSync(file);
    if (stat.isDirectory()) {
      for (const entry of readdirSync(file, { withFileTypes: true })) {
        if (!entry.isSymbolicLink()) measure(join(file, entry.name));
      }
    } else { removed.files++; removed.bytes += stat.size; }
  }
  function walk(folder) {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) continue;
      const file = join(folder, entry.name);
      const path = parts(root, file);
      const site = path.indexOf('site-packages');
      const library = site < 0 ? '' : path[site + 1]?.toLowerCase();
      const drop = entry.isDirectory()
        ? entry.name === '__pycache__' || (entry.name === 'tests' && testPackages.has(library))
        : /\.py[co]$/.test(entry.name) && existsSync(file.slice(0, -1));
      if (drop) {
        const within = relative(root, resolve(file));
        if (!within || within.startsWith('..') || isAbsolute(within)) throw new Error(`Unsafe prune path: ${file}`);
        measure(file);
        rmSync(file, { recursive: entry.isDirectory(), force: true });
      } else if (entry.isDirectory()) walk(file);
    }
  }
  walk(root);
  return removed;
}
