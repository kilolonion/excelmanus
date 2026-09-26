import { normalizeExcelPath } from "@/lib/api";
import { useExcelStore } from "@/stores/excel-store";
import { useFilePreviewStore } from "@/stores/file-preview-store";
import { useWordStore } from "@/stores/word-store";

/**
 * 文件（或文件夹）删除后的统一清理入口。
 *
 * 侧边栏删除、Agent delete_file、外部删除（SSE mutation 回声）都走这里，
 * 保证文件面板、最近打开、已打开的表格/文档标签、全屏视图与预览弹窗
 * 在同一时刻剔除同一份路径，不出现“一处已删、另一处还在”的口径混乱。
 *
 * paths 可以是文件或文件夹；文件夹的全部后代一并视为已删除。
 */
export function handleWorkspaceFilesDeleted(
  paths: string[],
  workspaceKey?: string | null,
): void {
  const targets = paths.filter(
    (path) => typeof path === "string" && normalizeExcelPath(path).length > 0,
  );
  if (targets.length === 0) return;

  useExcelStore.getState().handleFilesDeleted(targets, workspaceKey);
  useWordStore.getState().handleFilesDeleted(targets);

  const preview = useFilePreviewStore.getState();
  const isDeleted = (candidate: string | null | undefined): boolean => {
    if (!candidate) return false;
    const bare = normalizeExcelPath(candidate);
    return targets.some((target) => {
      const bareTarget = normalizeExcelPath(target);
      return bare === bareTarget || bare.startsWith(`${bareTarget}/`);
    });
  };
  if (preview.textOpen && isDeleted(preview.textTarget?.path)) preview.closeText();
  if (preview.imageOpen && isDeleted(preview.imageTarget?.path)) preview.closeImage();
  // 代码/文本预览标签也要同步剔除，否则弹窗里仍能切换到已删除文件。
  for (const tab of preview.previewTabs) {
    if (isDeleted(tab.filePath)) {
      preview.removePreviewTab({ filePath: tab.filePath, sessionId: tab.sessionId, workspaceId: tab.workspaceId });
    }
  }
}
