import { execFileSync } from 'node:child_process';

export const bundledNodeVersion = 'v22.23.2';

export function assertNodeIdentity(actual, expected = {
  version: bundledNodeVersion, platform: process.platform, arch: process.arch,
}) {
  for (const field of ['version', 'platform', 'arch']) {
    if (actual?.[field] !== expected[field]) {
      throw new Error(`Bundled Node ${field}: expected ${expected[field]}, found ${actual?.[field] ?? 'missing'}`);
    }
  }
  return actual;
}

export function checkNodeRuntime(executable) {
  const output = execFileSync(executable, ['-p', 'JSON.stringify({version:process.version,platform:process.platform,arch:process.arch})'], {
    encoding: 'utf8', windowsHide: true, timeout: 15_000,
  });
  return assertNodeIdentity(JSON.parse(output.trim()));
}
