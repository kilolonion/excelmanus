const fs = require("node:fs/promises");
const { createReadStream } = require("node:fs");
const path = require("node:path");
const { createHash } = require("node:crypto");

function trustedAssetRedirect(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password && !url.port &&
      ["github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"].includes(url.hostname);
  } catch { return false; }
}

function downloadError(error) {
  if (error?.code === "ENOSPC") return "磁盘空间不足，请释放空间后重新下载";
  if (["EACCES", "EPERM", "EROFS"].includes(error?.code)) return "无法写入更新目录，请检查目录权限或安全软件拦截后重试";
  if (error?.name === "TimeoutError") return "下载连接超时或长时间无响应，请检查网络、系统代理后重试";
  if (error?.name === "TypeError") return "下载连接中断，请检查网络、系统代理或证书配置后重试";
  return error?.message || "下载失败，请稍后重试";
}

async function hashFile(filename) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filename)) hash.update(chunk);
  return hash.digest("hex");
}

// Each attempt owns a private directory. Never overwrite a user-selected path,
// execute partial downloads, buffer a multi-GB installer, or bypass TLS checks.
async function downloadInstaller({ asset, directory, fetchImpl, signal, onProgress,
  idleTimeoutMs = 60_000 }) {
  let attemptDir;
  let handle;
  let reader;
  let response;
  let timer;
  const timeout = new AbortController();
  const combined = AbortSignal.any([signal, timeout.signal]);
  const touch = () => {
    clearTimeout(timer);
    timer = setTimeout(() => timeout.abort(new DOMException("下载无响应", "TimeoutError")), idleTimeoutMs);
  };
  try {
    await fs.mkdir(directory, { recursive: true, mode: 0o700 });
    attemptDir = await fs.mkdtemp(path.join(directory, "installer-"));
    const expected = Number.isSafeInteger(asset.size) && asset.size > 0 ? asset.size : null;
    if (expected && fs.statfs) {
      const disk = await fs.statfs(attemptDir);
      if (disk.bavail * disk.bsize < expected + 16 * 1024 * 1024) {
        throw Object.assign(new Error("磁盘空间不足"), { code: "ENOSPC" });
      }
    }
    touch();
    let url = asset.browser_download_url;
    for (let redirects = 0; redirects <= 5; redirects++) {
      combined.throwIfAborted();
      if (!trustedAssetRedirect(url)) throw new Error("安装包下载地址不受信任，已停止下载");
      response = await fetchImpl(url, { signal: combined, redirect: "manual", cache: "no-store",
        headers: { Accept: "application/octet-stream" } });
      if (![301, 302, 303, 307, 308].includes(response.status)) break;
      const location = response.headers.get("location");
      await response.body?.cancel();
      if (!location || redirects === 5) throw new Error("安装包下载重定向异常，请重试或查看发布页面");
      url = new URL(location, url).href;
    }
    if (response.status !== 200 || !response.body) {
      throw new Error(`安装包下载失败（HTTP ${response.status}），请检查网络或稍后重试`);
    }
    const contentType = response.headers.get("content-type") || "";
    if (/text\/|json|xml/i.test(contentType)) throw new Error("下载服务返回了网页而非安装包，请检查代理或登录门户");
    const length = Number(response.headers.get("content-length"));
    const headerSize = Number.isSafeInteger(length) && length > 0 ? length : null;
    if (expected && headerSize && expected !== headerSize) throw new Error("安装包大小与发布信息不一致，请重新检查更新");
    const total = expected || headerSize;
    const partial = path.join(attemptDir, "download.part");
    const filename = path.join(attemptDir, asset.name);
    handle = await fs.open(partial, "wx", 0o600);
    reader = response.body.getReader();
    const hash = createHash("sha256");
    let received = 0;
    const start = Date.now();
    let lastEvent = 0;
    const report = (force = false) => {
      const now = Date.now();
      if (!force && now - lastEvent < 150) return;
      lastEvent = now;
      onProgress({ received, total, percent: total ? Math.min(100, received / total * 100) : null,
        bytesPerSecond: Math.round(received * 1000 / Math.max(1, now - start)) });
    };
    report(true);
    while (true) {
      combined.throwIfAborted();
      touch();
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (total && received > total) throw new Error("安装包大小超出发布信息，已停止下载");
      hash.update(value);
      // FileHandle.write may perform a short write; don't silently truncate.
      let offset = 0;
      while (offset < value.byteLength) {
        const { bytesWritten } = await handle.write(value, offset, value.byteLength - offset);
        if (!bytesWritten) throw new Error("安装包写入失败，请检查磁盘");
        offset += bytesWritten;
      }
      report();
    }
    combined.throwIfAborted();
    clearTimeout(timer);
    if (!received || (total && received !== total)) throw new Error("安装包下载不完整，请重试");
    const digest = hash.digest("hex");
    if (asset.digest && (!/^sha256:[a-f0-9]{64}$/i.test(asset.digest) ||
        asset.digest.slice(7).toLowerCase() !== digest)) throw new Error("安装包校验失败，请重新下载");
    // Without either the release size or digest, EOF alone is not proof of a
    // complete installer (some proxies truncate responses without an error).
    if (!expected && !asset.digest) throw new Error("发布信息缺少安装包大小和校验值，请从发布页面手动下载");
    await handle.sync();
    await handle.close();
    handle = null;
    await fs.rename(partial, filename);
    report(true);
    return { filename, digest, size: received };
  } catch (error) {
    await reader?.cancel().catch(() => {});
    if (!reader) await response?.body?.cancel().catch(() => {});
    await handle?.close().catch(() => {});
    // Only the fresh directory allocated by this call is ever removed.
    if (attemptDir) await fs.rm(attemptDir, { recursive: true, force: true }).catch(() => {});
    if (signal.aborted) throw signal.reason;
    throw new Error(downloadError(timeout.signal.aborted ? timeout.signal.reason : error));
  } finally {
    clearTimeout(timer);
    reader?.releaseLock();
  }
}

async function verifyInstaller(download) {
  const stat = await fs.lstat(download.filename);
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== download.size ||
      await hashFile(download.filename) !== download.digest) {
    throw new Error("安装包已被修改或损坏，请重新下载");
  }
}

module.exports = { downloadInstaller, verifyInstaller, trustedAssetRedirect };
