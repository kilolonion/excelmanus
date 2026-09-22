export interface WebVersionIdentity {
  buildId: string | null;
  fingerprint: string | null;
  apiSchemaVersion: number | null;
}

export function versionDifference(baseline: WebVersionIdentity, remote: WebVersionIdentity) {
  const different = (a: string | number | null, b: string | number | null) => a !== null && b !== null && a !== b;
  return {
    changed: different(baseline.buildId, remote.buildId) || different(baseline.fingerprint, remote.fingerprint),
    incompatible: different(baseline.apiSchemaVersion, remote.apiSchemaVersion),
  };
}

export async function fetchWebBuild(): Promise<string | null> {
  // Older Android WebViews/Safari lack AbortSignal.timeout; don't turn every
  // successful upgrade into an endless wait on those clients.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 5_000);
  try {
    const response = await fetch("/api/app-version", { cache: "no-store", signal: controller.signal });
    if (!response.ok) return null;
    const data = await response.json();
    return typeof data?.buildId === "string" && data.buildId ? data.buildId : null;
  } finally { clearTimeout(timer); }
}
