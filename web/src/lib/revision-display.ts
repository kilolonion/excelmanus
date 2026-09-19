import type { OperationChange, OperationRecord, WorkbookRevisionItem } from "@/lib/api";
import { displayFileName, identityKey, toPublicFileIdentity } from "@/lib/file-identity";

const REASON_LABELS: Record<string, string> = {
  beforeEdit: "编辑前",
  afterEdit: "编辑后",
  beforeRestore: "恢复前",
  checkpoint: "检查点",
};

export function revisionReasonLabel(reason: string, label: string): string {
  if (label) return label;
  return REASON_LABELS[reason] ?? reason;
}

export function fileBaseName(path: string | null | undefined): string {
  if (!path) return "";
  return displayFileName(path) || path.replace(/\\/g, "/").split("/").pop() || path;
}

export function pathsReferToSameFile(a: string, b: string): boolean {
  const ia = toPublicFileIdentity(a);
  const ib = toPublicFileIdentity(b);
  if (ia && ib) return identityKey(ia) === identityKey(ib);
  return a.replace(/\\/g, "/").toLowerCase() === b.replace(/\\/g, "/").toLowerCase();
}

export function operationTouchesFile(op: OperationRecord, filePath: string | null | undefined): boolean {
  if (!filePath) return true;
  return op.changes.some((change) => pathsReferToSameFile(change.path, filePath));
}

export function isCurrentRevision(
  rec: WorkbookRevisionItem,
  currentVersion: string | null,
): boolean {
  if (!currentVersion || !rec.content_version) return false;
  return rec.content_version === currentVersion;
}

export function formatRelativeTime(iso: string | undefined, now = Date.now()): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const diff = now - d.getTime();
  if (diff < 45_000) return "刚刚";
  if (diff < 60 * 60 * 1000) return `${Math.max(1, Math.round(diff / 60_000))} 分钟前`;
  if (diff < 24 * 60 * 60 * 1000) return `${Math.max(1, Math.round(diff / 3_600_000))} 小时前`;
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfYesterday.getDate() - 1);
  const time = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  if (d.getTime() >= startOfYesterday.getTime() && d.getTime() < startOfToday.getTime()) {
    return `昨天 ${time}`;
  }
  return d.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatClockTime(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export type RevisionGroup = {
  key: string;
  title: string;
  createdAt?: string;
  items: WorkbookRevisionItem[];
};

export function groupRevisions(items: WorkbookRevisionItem[]): RevisionGroup[] {
  const sorted = [...items].sort((a, b) => b.sequence - a.sequence);
  const byTx = new Map<string, WorkbookRevisionItem[]>();
  for (const rec of sorted) {
    if (!rec.transaction_id) continue;
    const list = byTx.get(rec.transaction_id) ?? [];
    list.push(rec);
    byTx.set(rec.transaction_id, list);
  }

  const groups: RevisionGroup[] = [];
  const seenTx = new Set<string>();
  for (const rec of sorted) {
    if (rec.transaction_id && seenTx.has(rec.transaction_id)) continue;
    if (rec.transaction_id) {
      seenTx.add(rec.transaction_id);
      const members = (byTx.get(rec.transaction_id) ?? [rec]).sort((a, b) => b.sequence - a.sequence);
      groups.push({
        key: rec.transaction_id,
        title: groupTitle(members),
        createdAt: members[0]?.created_at,
        items: members,
      });
      continue;
    }
    groups.push({
      key: rec.revision_id,
      title: revisionReasonLabel(rec.reason, rec.label),
      createdAt: rec.created_at,
      items: [rec],
    });
  }
  return groups;
}

function groupTitle(members: WorkbookRevisionItem[]): string {
  const newest = members[0];
  if (newest?.label) return newest.label;
  const reasons = new Set(members.map((m) => m.reason));
  if (reasons.has("beforeEdit") && reasons.has("afterEdit")) return "一次编辑";
  if (reasons.has("beforeRestore")) return "一次恢复";
  return revisionReasonLabel(newest?.reason ?? "", newest?.label ?? "");
}

export function shortContentVersion(version: string | null | undefined): string {
  if (!version) return "";
  if (version.startsWith("sha256:")) return version.slice(7, 15);
  return version.slice(0, 8);
}

export function changeSummary(changes: OperationChange[]): string {
  const added = changes.filter((c) => c.change_type === "added").length;
  const modified = changes.filter((c) => c.change_type === "modified").length;
  const deleted = changes.filter((c) => c.change_type === "deleted").length;
  const parts: string[] = [];
  if (modified > 0) parts.push(`${modified} 修改`);
  if (added > 0) parts.push(`${added} 新增`);
  if (deleted > 0) parts.push(`${deleted} 删除`);
  return parts.join(" · ") || "无文件变更";
}
