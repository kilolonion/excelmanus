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
  const response = await fetch("/api/app-version", { cache: "no-store", signal: AbortSignal.timeout(5_000) });
  if (!response.ok) return null;
  const data = await response.json();
  return typeof data.buildId === "string" && data.buildId ? data.buildId : null;
}
