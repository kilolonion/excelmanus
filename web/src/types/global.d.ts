/* 全局类型声明 */

interface ExcelManusDesktopPickedFile {
  name: string;
  type?: string;
  data: ArrayBuffer | Uint8Array;
}

interface ExcelManusDesktopBridge {
  mobilePairing?: (action: import("@/lib/mobile-pairing").MobilePairingAction, input?: { id?: string; address?: string }) => Promise<import("@/lib/mobile-pairing").MobilePairingStatus>;
  selectFolder: () => Promise<string | null>;
  pickChatFiles: () => Promise<{
    files: ExcelManusDesktopPickedFile[];
    skipped: string[];
  }>;
}

interface Window {
  excelManusDesktop?: ExcelManusDesktopBridge;
  /** Injected by the Android client only in its selected server's main frame. */
  excelManusAndroid?: {
    version: 1;
    saveBlob: (blob: Blob, filename: string) => void;
    scanPairing?: () => void;
    openConnectionSettings?: () => void;
  };
}
