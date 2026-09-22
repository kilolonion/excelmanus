const { contextBridge, ipcRenderer } = require("electron");

ipcRenderer.on("excelmanus:menu-action", (_event, action) => {
  if (typeof action !== "string") return;
  window.dispatchEvent(new CustomEvent("excelmanus:menu-action", { detail: action }));
});

contextBridge.exposeInMainWorld("excelManusDesktop", {
  selectFolder: () => ipcRenderer.invoke("excelmanus:select-folder"),
  pickChatFiles: () => ipcRenderer.invoke("excelmanus:pick-chat-files"),
  mobilePairing: (action, input) => ipcRenderer.invoke("excelmanus:mobile-pairing", action, input),
  checkUpdate: () => ipcRenderer.invoke("excelmanus:check-update"),
  downloadUpdate: () => ipcRenderer.invoke("excelmanus:download-update"),
});
