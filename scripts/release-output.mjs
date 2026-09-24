import { existsSync, mkdtempSync, renameSync, rmSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';

// Publish only a completed staging directory. Preserve the previous deliverable
// until the replacement rename succeeds, including Windows file-lock failures.
export function publishRelease(staging, destination, io = { renameSync, rmSync }) {
  staging = resolve(staging);
  destination = resolve(destination);
  if (staging === destination || dirname(staging) !== dirname(destination)) {
    throw new Error('Release staging and destination must be distinct sibling directories');
  }
  for (const name of ['release-manifest.json', 'SHA256SUMS.txt']) {
    if (!statSync(join(staging, name)).isFile()) throw new Error(`Incomplete release: ${name}`);
  }
  const backup = existsSync(destination)
    ? mkdtempSync(join(dirname(destination), '.release-backup-')) : null;
  const previous = backup && join(backup, 'previous');
  let movedPrevious = false;
  try {
    if (previous) { io.renameSync(destination, previous); movedPrevious = true; }
    io.renameSync(staging, destination);
  } catch (error) {
    if (movedPrevious) {
      try { io.renameSync(previous, destination); }
      catch (rollbackError) {
        throw new AggregateError([error, rollbackError], `Release replacement and rollback failed; previous release is preserved at ${previous}`);
      }
    }
    if (backup) { try { io.rmSync(backup, { recursive: true }); } catch {} }
    throw error;
  }
  if (backup) {
    try { io.rmSync(backup, { recursive: true }); }
    catch { return { retainedBackup: previous }; }
  }
  return { retainedBackup: null };
}
