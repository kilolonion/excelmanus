/** Keep the object URL alive until Chromium has consumed the download click.
 * In Electron the native save dialog may open after this function returns.
 */
export function saveBlob(blob: Blob, filename: string): void {
  const safeName = filename.replace(/\\/g, "/").split("/").pop() || "download";
  if (typeof window !== "undefined" && window.excelManusAndroid?.version === 1) {
    window.excelManusAndroid.saveBlob(blob, safeName);
    return;
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = safeName;
  document.body.appendChild(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  }
}
