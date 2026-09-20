const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("excelManusDesktop", {
  selectFolder: () => ipcRenderer.invoke("excelmanus:select-folder"),
});
