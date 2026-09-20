function closeWindow(window, timeoutMs) {
  if (window.isDestroyed()) return Promise.resolve(true);
  return new Promise((resolve, reject) => {
    const contents = window.webContents;
    const finish = (closed, error) => {
      clearTimeout(timer);
      window.removeListener("closed", onClosed);
      window.removeListener("close", onClose);
      contents.removeListener("will-prevent-unload", onPrevented);
      if (error) reject(error);
      else resolve(closed);
    };
    const onClosed = () => finish(true);
    const onPrevented = () => finish(false);
    const onClose = (event) => {
      // Other native listeners can also cancel closing, without beforeunload.
      queueMicrotask(() => { if (event.defaultPrevented) finish(false); });
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    window.once("closed", onClosed);
    window.on("close", onClose);
    contents.once("will-prevent-unload", onPrevented);
    try { window.close(); } catch (error) { finish(false, error); }
  });
}

async function closeWindowsBeforeShutdown(windows, timeoutMs = 15_000) {
  // Let beforeunload protect pending workbook edits. Never destroy a renderer
  // or stop its backend while the page has cancelled closing.
  for (const window of windows) {
    if (!await closeWindow(window, timeoutMs)) return false;
  }
  return true;
}

module.exports = { closeWindowsBeforeShutdown };
