// Keep handoff order independently testable without running an installer.
async function installDownloadedUpdate({ closeWindows, stopServices, launch, recover, exit }) {
  if (!await closeWindows()) return false;
  try {
    await stopServices();
    const error = await launch();
    if (error) throw new Error(error);
  } catch (error) {
    try { await recover(); }
    catch (recoveryError) { throw new Error(`${error.message}；恢复应用失败：${recoveryError.message}`); }
    throw error;
  }
  exit();
  return true;
}

module.exports = { installDownloadedUpdate };
