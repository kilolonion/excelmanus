const { readdir, writeFile, mkdir } = require('node:fs/promises');
const path = require('node:path');

const MANIFEST = 'excelmanus-installed-files.txt';

async function collectFiles(root, relative = '') {
  const files = [];
  for (const entry of await readdir(path.join(root, relative), { withFileTypes: true })) {
    const name = path.join(relative, entry.name);
    if (entry.isSymbolicLink()) throw new Error(`Installer payload must not contain links: ${name}`);
    if (entry.isDirectory()) files.push(...await collectFiles(root, name));
    else if (entry.isFile() && name !== MANIFEST) files.push(name.replaceAll('/', '\\'));
  }
  return files.sort();
}

async function afterPack(context) {
  if (context.electronPlatformName !== 'win32') return;
  // Pinned electron-builder 26.15.3's modern 7za chooses BCJ2 at -mx=9;
  // its install-time Nsis7z can silently omit those PE entries (#9983).
  // Set this in the hook so npm, release scripts and direct builder calls all
  // use the compatible encoder before buildAppPackage runs.
  process.env.ELECTRON_BUILDER_7Z_FILTER = 'BCJ';
  const files = await collectFiles(context.appOutDir);
  const executable = `${context.packager.appInfo.productFilename}.exe`;
  if (!files.includes(executable)) throw new Error('ExcelManus executable missing from installer manifest');
  // NSIS copies its elevation helper after afterPack, before archiving.
  if (!files.includes('resources\\elevate.exe')) files.push('resources\\elevate.exe');
  // NSIS uses UTF-16LE so non-ASCII package paths survive every system locale.
  const content = '\ufeff' + files.join('\r\n') + '\r\n';
  await writeFile(path.join(context.appOutDir, MANIFEST), content, 'utf16le');
  const staging = path.join(context.packager.projectDir, '.build');
  await mkdir(staging, { recursive: true });
  await writeFile(path.join(staging, MANIFEST), content, 'utf16le');
}

module.exports = afterPack;
module.exports.collectFiles = collectFiles;
module.exports.MANIFEST = MANIFEST;
