const RELEASES_URL = "https://github.com/kilolonion/excelmanus/releases";
const RELEASE_API = "https://api.github.com/repos/kilolonion/excelmanus/releases/latest";

function parseVersion(value) {
  const match = /^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([\w.-]+))?(?:\+[\w.-]+)?$/.exec(value);
  if (!match) throw new Error("发布版本号格式无效");
  return { numbers: match.slice(1, 4).map(Number), prerelease: match[4] };
}

function isNewer(latest, current) {
  const a = parseVersion(latest);
  const b = parseVersion(current);
  for (let i = 0; i < 3; i++) {
    if (a.numbers[i] !== b.numbers[i]) return a.numbers[i] > b.numbers[i];
  }
  return !a.prerelease && !!b.prerelease;
}

function trustedReleaseUrl(value, download = false) {
  try {
    const url = new URL(value);
    const prefix = download ? "/kilolonion/excelmanus/releases/download/" : "/kilolonion/excelmanus/releases/tag/";
    return url.protocol === "https:" && url.hostname === "github.com" && !url.port &&
      !url.username && !url.password && url.pathname.startsWith(prefix) ? url.href : null;
  } catch { return null; }
}

function selectInstaller(assets, platform, arch) {
  return assets.find(asset => {
    if (!asset || typeof asset.name !== "string") return false;
    if (asset.state !== "uploaded" || !trustedReleaseUrl(asset.browser_download_url, true)) return false;
    if (platform === "win32" && arch === "x64") {
      return /^ExcelManus[ ._-]+Setup[ ._-]+.*\.exe$/i.test(asset.name) && !/arm64|ia32/i.test(asset.name);
    }
    if (platform === "darwin") {
      return /^ExcelManus[ ._-].*\.dmg$/i.test(asset.name) &&
        (/universal/i.test(asset.name) || (arch === "arm64" ? /arm64/i.test(asset.name)
          : arch === "x64" && (/x64/i.test(asset.name) || /^ExcelManus[ ._-]+\d+\.\d+\.\d+\.dmg$/i.test(asset.name))));
    }
    return false;
  });
}

function createUpdateService({ current, platform = process.platform, arch = process.arch, fetchImpl = fetch, openExternal }) {
  let checked = null;
  let pending = null;
  return {
    async check() {
      if (pending) return pending;
      pending = (async () => {
        // Clear stale downloads when a later check fails.
        checked = null;
        let response;
        try {
          response = await fetchImpl(RELEASE_API, {
            headers: { Accept: "application/vnd.github+json", "User-Agent": `ExcelManus/${current}` },
            signal: AbortSignal.timeout(15_000),
            redirect: "error",
          });
        } catch {
          throw new Error("无法连接更新服务，请检查网络后重试，或打开发布页面下载");
        }
        if (response.status === 404) throw new Error("尚未发布可用的正式版本，请稍后重试或查看下载页面");
        if (!response.ok) throw new Error(`无法检查更新（HTTP ${response.status}），请稍后重试`);
        const release = await response.json();
        const releaseUrl = trustedReleaseUrl(release.html_url);
        if (release.draft || release.prerelease || !releaseUrl || parseVersion(release.tag_name).prerelease) {
          throw new Error("发布信息无效，请到下载页面查看");
        }
        const installer = selectInstaller(Array.isArray(release.assets) ? release.assets : [], platform, arch);
        checked = {
          current, latest: release.tag_name.replace(/^v/, ""),
          hasUpdate: isNewer(release.tag_name, current),
          releaseNotes: typeof release.body === "string" ? release.body.slice(0, 20_000) : "",
          releaseUrl, downloadUrl: installer?.browser_download_url || null,
          installerName: installer?.name || null, platform,
        };
        return checked;
      })();
      try { return await pending; } finally { pending = null; }
    },
    async download() {
      if (!checked?.hasUpdate || !checked.downloadUrl) throw new Error("请先检查更新，确认有适用的新版安装包");
      // The renderer cannot supply a URL or a command to execute.
      await openExternal(checked.downloadUrl);
    },
  };
}

module.exports = { createUpdateService, isNewer, selectInstaller, trustedReleaseUrl, RELEASES_URL };
