"use client";

import { useEffect, useState } from "react";

export function useDesktopUpdate() {
  const [status, setStatus] = useState<ExcelManusDesktopUpdateStatus | null>(null);
  useEffect(() => {
    const bridge = window.excelManusDesktop;
    if (!bridge?.getUpdateStatus || !bridge.onUpdateStatus) return;
    let mounted = true;
    const receive = (next: ExcelManusDesktopUpdateStatus) => {
      if (mounted) setStatus(previous => !previous || next.revision >= previous.revision ? next : previous);
    };
    // Subscribe before taking the snapshot; revisions reject stale responses.
    const unsubscribe = bridge.onUpdateStatus(receive);
    void bridge.getUpdateStatus().then(receive).catch(() => {});
    return () => { mounted = false; unsubscribe(); };
  }, []);
  return status;
}
