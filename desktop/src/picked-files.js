const { open } = require("node:fs/promises");
const path = require("node:path");

const MIME_TYPES = {
  ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
  ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp", ".svg": "image/svg+xml",
  ".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/csv", ".json": "application/json",
  ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
};

async function readPickedFiles(paths, maxBytes = 256 * 1024 * 1024) {
  const files = [];
  const skipped = [];
  let remaining = maxBytes;
  for (const filePath of paths) {
    let handle;
    try {
      handle = await open(filePath, "r");
      const stat = await handle.stat();
      if (!stat.isFile() || stat.size > remaining) {
        skipped.push(path.basename(filePath));
        continue;
      }
      // Bound the allocation even if a selected file grows while being read.
      const data = Buffer.alloc(stat.size);
      let offset = 0;
      while (offset < data.length) {
        const { bytesRead } = await handle.read(data, offset, data.length - offset, offset);
        if (!bytesRead) break;
        offset += bytesRead;
      }
      const finalStat = await handle.stat();
      if (offset !== stat.size || finalStat.size !== stat.size || finalStat.mtimeMs !== stat.mtimeMs) {
        skipped.push(path.basename(filePath));
        continue;
      }
      remaining -= data.length;
      files.push({ name: path.basename(filePath), type: MIME_TYPES[path.extname(filePath).toLowerCase()] || "application/octet-stream", data });
    } catch {
      skipped.push(path.basename(filePath));
    } finally {
      await handle?.close();
    }
  }
  return { files, skipped };
}

module.exports = { readPickedFiles };
