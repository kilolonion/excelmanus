/* Injected at document start, into the selected server's main frame only. */
(function () {
  "use strict";
  if (window !== window.top || !window.ExcelManusNative) return;
  const config = window.__EXCELMANUS_ANDROID_CONFIG__;
  delete window.__EXCELMANUS_ANDROID_CONFIG__;
  if (!config || window.location.origin !== config.origin) return;
  if (config.token) sessionStorage.setItem("excelmanus_manage_token", config.token);
  else sessionStorage.removeItem("excelmanus_manage_token");
  // The online client always uses the selected site's same-origin API proxy.
  // Ignore server-side loopback settings injected later by Next.js.
  const runtime = Object.freeze({ backendOrigin: "same-origin" });
  Object.defineProperty(window, "__EXCELMANUS_RUNTIME__", {
    configurable: false, get: () => runtime, set: () => {},
  });
  const native = window.ExcelManusNative;
  const pending = new Map();
  let serial = 0;
  let saving = false;
  native.onmessage = (event) => {
    let result;
    try { result = JSON.parse(event.data); } catch { return; }
    const call = pending.get(result.requestId);
    if (!call) return;
    clearTimeout(call.timer);
    pending.delete(result.requestId);
    if (result.ok) call.resolve(result);
    else call.reject(new Error(result.error || "文件保存失败，请重试。"));
  };
  function send(type, payload, timeout = 60000) {
    const requestId = String(++serial);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(requestId);
        reject(new Error("操作超时，请重试。"));
      }, timeout);
      pending.set(requestId, { resolve, reject, timer });
      try { native.postMessage(JSON.stringify({ type, requestId, ...payload })); }
      catch (error) { clearTimeout(timer); pending.delete(requestId); reject(error); }
    });
  }
  async function save(blob, filename) {
    if (saving) throw new Error("已有文件正在保存，请稍候。" );
    if (blob.size > 256 * 1024 * 1024) throw new Error("单个文件最多保存 256 MB。" );
    saving = true;
    const transferId = "file-" + Date.now() + "-" + (++serial);
    try {
      await send("saveBegin", { transferId, filename, mime: blob.type || "application/octet-stream", size: blob.size });
      let sequence = 0;
      for (let offset = 0; offset < blob.size; offset += 49152) {
        const bytes = new Uint8Array(await blob.slice(offset, offset + 49152).arrayBuffer());
        let binary = "";
        for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
        await send("saveChunk", { transferId, sequence: sequence++, data: btoa(binary) });
      }
      return await send("saveFinish", { transferId }, 10 * 60 * 1000);
    } catch (error) {
      // Also abort on native cancellation, navigation, and a missing acknowledgement.
      void send("saveAbort", { transferId }).catch(() => {});
      throw error;
    } finally { saving = false; }
  }
  function report(error) {
    void send("notice", { message: error instanceof Error ? error.message : "文件保存失败，请重试。" }).catch(() => {});
  }
  function copyText(value) {
    const text = String(value);
    if (text.length > 16384 || JSON.stringify(text).length > 65000) {
      return Promise.reject(new Error("一次最多复制 16,384 个字符，请缩小选择范围。"));
    }
    return send("copyText", { text }).then(() => undefined);
  }
  // Async Clipboard is absent on HTTP LAN origins. Offer write-only native
  // compatibility; never expose clipboard reads to the page.
  if (!navigator.clipboard) {
    Object.defineProperty(navigator, "clipboard", { value: Object.freeze({ writeText: copyText }), configurable: true });
  }
  function handleBack() {
    const overlay = document.querySelector('[role=menu][data-state=open],[role=listbox][data-state=open]')
      || document.querySelector('[role=dialog][data-state=open],[role=alertdialog]');
    if (overlay) {
      overlay.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", code: "Escape", bubbles: true, cancelable: true }));
      return true;
    }
    const sidebar = document.querySelector('aside[aria-label="侧栏"][aria-hidden=false]');
    if (sidebar && getComputedStyle(sidebar).position === "fixed") {
      const close = sidebar.querySelector('button[aria-label="收起侧栏"]');
      if (close) { close.click(); return true; }
    }
    return false;
  }
  Object.defineProperty(window, "excelManusAndroid", {
    configurable: false,
    value: Object.freeze({
      version: 1,
      copyText,
      handleBack,
      scanPairing: () => { void send("scanPairing", {}).catch(report); },
      openConnectionSettings: () => { void send("connectionSettings", {}).catch(report); },
      saveBlob: (blob, filename) => { void save(blob, filename).catch(report); },
    }),
  });
  // Compatibility with deployed versions that still use <a download blob:...>.
  document.addEventListener("click", (event) => {
    const anchor = event.target && event.target.closest ? event.target.closest("a[download]") : null;
    if (!anchor || !anchor.href.startsWith("blob:")) return;
    event.preventDefault();
    void fetch(anchor.href).then((response) => response.blob()).then((blob) => save(blob, anchor.download || "download")).catch(report);
  }, true);
  window.addEventListener("pagehide", () => {
    pending.forEach((call) => { clearTimeout(call.timer); call.reject(new Error("页面已关闭。")); });
    pending.clear();
  });
})();
