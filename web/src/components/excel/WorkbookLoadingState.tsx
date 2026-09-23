"use client";

import { FileSpreadsheet, Loader2 } from "lucide-react";
import styles from "./WorkbookLoadingState.module.css";

export function WorkbookLoadingState({
  label = "正在准备表格",
  detail = "正在启动表格引擎",
  compact = false,
}: {
  label?: string;
  detail?: string;
  compact?: boolean;
}) {
  return (
    <div className={compact ? styles.compact : styles.root} role="status" aria-live="polite">
      {!compact && <div className={styles.skeleton} aria-hidden="true" />}
      <div className={styles.card}>
        <div className={styles.heading}>
          <span className={styles.icon} aria-hidden="true"><FileSpreadsheet size={compact ? 15 : 18} /></span>
          <span className={styles.copy}>
            <span className={styles.label}>{label}</span>
            <span className={styles.detail}>{detail}</span>
          </span>
          <Loader2 className={styles.spinner} size={compact ? 15 : 18} aria-hidden="true" />
        </div>
        <div className={styles.progress} aria-hidden="true" />
      </div>
    </div>
  );
}
