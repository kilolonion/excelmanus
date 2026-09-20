const { test } = require('node:test');
const assert = require('node:assert/strict');
const { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { dirname, join, resolve } = require('node:path');

test('runtime pruning keeps executable dependencies and data while removing build/test payloads', async () => {
  const { pythonBaseFilter, frontendRuntimeFilter, prunePythonRuntime } = await import('../scripts/runtime-files.mjs');
  const temp = resolve(tmpdir());
  const work = mkdtempSync(join(temp, 'excelmanus-runtime-'));
  const put = (root, path) => {
    const file = join(root, path);
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, path);
  };
  try {
    const source = join(work, 'source');
    const python = join(work, 'python');
    for (const file of ['python.exe', 'Lib/json/__init__.py', 'Lib/test/test_json.py', 'Lib/ensurepip/__init__.py', 'Lib/idlelib/editor.py', 'Lib/site-packages/pip/__init__.py']) put(source, file);
    cpSync(source, python, { recursive: true, filter: file => pythonBaseFilter(source, file) });
    assert.ok(existsSync(join(python, 'Lib/json/__init__.py')));
    for (const file of ['Lib/test', 'Lib/ensurepip', 'Lib/idlelib', 'Lib/site-packages/pip']) assert.ok(!existsSync(join(python, file)), file);
    const retained = [
      'numpy/testing/__init__.py', 'pandas/_testing/__init__.py', 'scipy/_lib/_testutils.py',
      'sklearn/datasets/data/iris.csv', 'matplotlib/mpl-data/fonts/ttf/font.ttf',
      'numpy.libs/openblas.dll', 'numpy-1.dist-info/licenses/LICENSE',
      'custom_library/tests/runtime.py', 'custom_library/sourceless.pyc',
    ];
    const removed = ['numpy/tests/test_array.py', 'scipy/linalg/tests/test_solve.py', 'numpy/__pycache__/array.cpython-312.pyc', 'numpy/array.pyc'];
    for (const file of [...retained, ...removed, 'numpy/array.py']) put(python, `Lib/site-packages/${file}`);
    const stats = prunePythonRuntime(python);
    assert.equal(stats.files, removed.length);
    for (const file of retained) assert.equal(readFileSync(join(python, 'Lib/site-packages', file), 'utf8'), `Lib/site-packages/${file}`);
    for (const file of removed) assert.ok(!existsSync(join(python, 'Lib/site-packages', file)), file);

    const frontend = join(work, 'frontend');
    const kept = ['server.js', '.next/server/app/tests/page.js', 'public/test/data.json', 'node_modules/next/server.js', 'node_modules/next/package.json', 'node_modules/@img/sharp/lib.dll', 'node_modules/next/LICENSE'];
    const dropped = ['.next/cache/build.bin', 'node_modules/next/tests/test.js', 'node_modules/next/index.d.ts', 'node_modules/next/index.d.mts', 'node_modules/next/server.js.map'];
    for (const file of [...kept, ...dropped]) put(frontend, file);
    const staged = join(work, 'staged');
    cpSync(frontend, staged, { recursive: true, filter: file => frontendRuntimeFilter(frontend, file) });
    for (const file of kept) assert.equal(readFileSync(join(staged, file), 'utf8'), file);
    for (const file of dropped) assert.ok(!existsSync(join(staged, file)), file);
  } finally {
    assert.equal(dirname(work), temp);
    rmSync(work, { recursive: true, force: true });
  }
});
