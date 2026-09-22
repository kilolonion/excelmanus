/* 全局类型声明 */

interface ExcelManusDesktopPickedFile {
  name: string;
  type?: string;
  data: ArrayBuffer | Uint8Array;
}

interface ExcelManusDesktopBridge {
  checkUpdate?: () => Promise<ExcelManusDesktopUpdate>;
  downloadUpdate?: () => Promise<ExcelManusDesktopUpdateStatus | void>;
  getUpdateStatus?: () => Promise<ExcelManusDesktopUpdateStatus>;
  onUpdateStatus?: (callback: (status: ExcelManusDesktopUpdateStatus) => void) => () => void;
  cancelUpdate?: () => Promise<void>;
  installUpdate?: () => Promise<ExcelManusDesktopUpdateStatus>;
  mobilePairing?: (action: import("@/lib/mobile-pairing").MobilePairingAction, input?: { id?: string; address?: string }) => Promise<import("@/lib/mobile-pairing").MobilePairingStatus>;
  selectFolder: () => Promise<string | null>;
  pickChatFiles: () => Promise<{
    files: ExcelManusDesktopPickedFile[];
    skipped: string[];
  }>;
}

interface ExcelManusDesktopUpdateStatus {
  revision: number;
  phase: "idle" | "downloading" | "verifying" | "ready" | "installing" | "cancelled" | "error";
  received: number;
  total: number | null;
  percent: number | null;
  bytesPerSecond: number;
  error: string;
  latest?: string;
  installerName?: string;
}

interface ExcelManusDesktopUpdate {
  current: string;
  latest: string;
  hasUpdate: boolean;
  releaseNotes: string;
  releaseUrl: string;
  downloadUrl: string | null;
  installerName: string | null;
  platform: string;
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
