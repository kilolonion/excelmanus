const test = require("node:test");
const assert = require("node:assert/strict");
const { mkdtemp, writeFile, rm } = require("node:fs/promises");
const { tmpdir } = require("node:os");
const path = require("node:path");
const { readPickedFiles } = require("../src/picked-files");

test("native uploads preserve Chinese filenames, image MIME types and file bytes", async (t) => {
  const folder = await mkdtemp(path.join(tmpdir(), "em-upload-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const file = path.join(folder, "测试图片.JPG");
  const bytes = Buffer.from([0xff, 0xd8, 0xff, 0xd9]);
  await writeFile(file, bytes);
  const result = await readPickedFiles([file]);
  assert.deepEqual(result.skipped, []);
  assert.equal(result.files[0].name, "测试图片.JPG");
  assert.equal(result.files[0].type, "image/jpeg");
  assert.deepEqual(result.files[0].data, bytes);
});

test("batch allocation is bounded and skipped files do not stop readable files", async (t) => {
  const folder = await mkdtemp(path.join(tmpdir(), "em-upload-"));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const first = path.join(folder, "one.txt"), second = path.join(folder, "two.txt"), last = path.join(folder, "last.txt");
  await Promise.all([writeFile(first, "123"), writeFile(second, "456"), writeFile(last, "7")]);
  const missing = path.join(folder, "missing.txt");
  const result = await readPickedFiles([first, second, missing, last], 4);
  assert.deepEqual(result.files.map((file) => file.name), ["one.txt", "last.txt"]);
  assert.deepEqual(result.skipped, ["two.txt", "missing.txt"]);
});
