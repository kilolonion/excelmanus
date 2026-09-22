const RELEASES_URL = "https://github.com/kilolonion/excelmanus/releases";
const RELEASE_API = "https://api.github.com/repos/kilolonion/excelmanus/releases/latest";
const { downloadInstaller, verifyInstaller } = require("./update-download");

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
    if (!asset || typeof asset.name !== "string" || /[<>:"/\\|?*\x00-\x1f]/.test(asset.name)) return false;
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

function createUpdateService({ current, platform = process.platform, arch = process.arch, fetchImpl = fetch,
  downloadDirectory, installImpl, onStatus = () => {}, idleTimeoutMs }) {
  let checked = null;
  let installer = null;
  let pending = null;
  let transfer = null;
  let installing = null;
  let controller = null;
  let downloaded = null;
  let state = { revision: 0, phase: "idle", received: 0, total: null, percent: null, bytesPerSecond: 0, error: "" };
  const publish = (patch) => {
    state = { ...state, ...patch, revision: state.revision + 1 };
    // A closed renderer must never turn a successful download into a failure.
    try { onStatus({ ...state }); } catch { /* renderer may be reloading */ }
  };
  const service = {
    status() { return { ...state }; },
    async check() {
      if (checked && (transfer || installing || downloaded)) return checked;
      if (pending) return pending;
      pending = (async () => {
        // Clear stale downloads when a later check fails.
        checked = null;
        installer = null;
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
        if (response.status === 403 || response.status === 429) {
          throw new Error("更新服务请求频率超限，请稍后重试，或打开发布页面下载");
        }
        if (!response.ok) throw new Error(`无法检查更新（HTTP ${response.status}），请稍后重试`);
        let release;
        try { release = await response.json(); } catch { throw new Error("更新服务返回了无效数据，请检查网络代理后重试"); }
        if (!release || typeof release.tag_name !== "string") throw new Error("发布信息无效，请到下载页面查看");
        const releaseUrl = trustedReleaseUrl(release.html_url);
        if (release.draft || release.prerelease || !releaseUrl || parseVersion(release.tag_name).prerelease) {
          throw new Error("发布信息无效，请到下载页面查看");
        }
        installer = selectInstaller(Array.isArray(release.assets) ? release.assets : [], platform, arch);
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
      if (transfer) return transfer;
      if (installing) return installing;
      if (downloaded) return service.install();
      if (!checked?.hasUpdate || !checked.downloadUrl) throw new Error("请先检查更新，确认有适用的新版安装包");
      if (!downloadDirectory || !installImpl) throw new Error("当前应用不支持自动更新，请从发布页面下载安装包");
      const asset = { ...installer };
      controller = new AbortController();
      publish({ phase: "downloading", latest: checked.latest, installerName: asset.name,
        received: 0, total: asset.size || null, percent: null, bytesPerSecond: 0, error: "" });
      transfer = (async () => {
        try {
          downloaded = await downloadInstaller({ asset, directory: downloadDirectory(), fetchImpl,
            signal: controller.signal, idleTimeoutMs, onProgress: progress => publish(progress) });
          publish({ phase: "ready", percent: 100 });
        } catch (error) {
          const cancelled = controller.signal.aborted;
          publish({ phase: cancelled ? "cancelled" : "error", error: cancelled ? "下载已取消，可以重新下载" : error.message });
          if (!cancelled) throw error;
          return service.status();
        } finally { controller = null; }
        return service.install();
      })();
      try { return await transfer; } finally { transfer = null; }
    },
    cancel() {
      if (state.phase === "downloading") controller?.abort(new DOMException("下载已取消", "AbortError"));
    },
    async install() {
      if (installing) return installing;
      if (!downloaded) throw new Error("请先完整下载安装包");
      installing = (async () => {
        publish({ phase: "verifying", error: "" });
        try { await verifyInstaller(downloaded); }
        catch {
          downloaded = null;
          const error = "安装包已丢失或损坏，请重新下载";
          publish({ phase: "error", error });
          throw new Error(error);
        }
        try {
          publish({ phase: "installing" });
          // This callback closes windows with beforeunload, stops bundled
          // services, launches the verified installer, and only then exits.
          const started = await installImpl(downloaded.filename, platform);
          if (started === false) publish({ phase: "ready", error: "退出已取消，请保存表格并等待任务完成后，点击退出并更新" });
        } catch (error) {
          publish({ phase: "ready", error: `无法启动安装：${error.message}。可重试或从发布页面手动安装` });
          throw error;
        }
        return service.status();
      })();
      try { return await installing; } finally { installing = null; }
    },
  };
  return service;
}

module.exports = { createUpdateService, isNewer, selectInstaller, trustedReleaseUrl, RELEASES_URL };
