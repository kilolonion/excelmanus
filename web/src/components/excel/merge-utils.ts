/**
 * 合并单元格工具函数 — 供 ExcelPreviewTable / ExcelDiffTable 等组件共用。
 *
 * 将后端 MergeRange[] 转为高效查询结构，支持：
 * - 判断某单元格是否是合并区域的主单元格（返回 colSpan/rowSpan）
 * - 判断某单元格是否是从属单元格（应隐藏渲染）
 */

import type { MergeRange } from "@/stores/excel-store";

export interface MergeSpan {
  colSpan: number;
  rowSpan: number;
}

/**
 * 构建合并单元格查询 Map。
 *
 * @param merges 后端返回的合并区域列表（1-based row/col）
 * @returns 两个 Map:
 *   - masterMap: key = "row,col" → MergeSpan（主单元格的跨度）
 *   - hiddenSet: Set of "row,col"（从属单元格，渲染时跳过）
 */
export function buildMergeMaps(merges: MergeRange[] | undefined): {
  masterMap: Map<string, MergeSpan>;
  hiddenSet: Set<string>;
} {
  const masterMap = new Map<string, MergeSpan>();
  const hiddenSet = new Set<string>();

  if (!merges || merges.length === 0) {
    return { masterMap, hiddenSet };
  }

  for (const mr of merges) {
    const rowSpan = mr.max_row - mr.min_row + 1;
    const colSpan = mr.max_col - mr.min_col + 1;

    // 主单元格（左上角）
    masterMap.set(`${mr.min_row},${mr.min_col}`, { rowSpan, colSpan });

    // 从属单元格
    for (let r = mr.min_row; r <= mr.max_row; r++) {
      for (let c = mr.min_col; c <= mr.max_col; c++) {
        if (r === mr.min_row && c === mr.min_col) continue;
        hiddenSet.add(`${r},${c}`);
      }
    }
  }

  return { masterMap, hiddenSet };
}

/**
 * 基于列字母索引的合并查询（用于 ExcelDiffTable 的 Grid 视图）。
 *
 * @param merges 合并区域
 * @param colIndexToNumber 列号（1-based 数字）
 * @param row 行号（1-based）
 */
export function getMergeInfo(
  masterMap: Map<string, MergeSpan>,
  hiddenSet: Set<string>,
  row: number,
  col: number,
): { isMaster: boolean; isHidden: boolean; span?: MergeSpan } {
  const key = `${row},${col}`;
  if (hiddenSet.has(key)) {
    return { isMaster: false, isHidden: true };
  }
  const span = masterMap.get(key);
  if (span) {
    return { isMaster: true, isHidden: false, span };
  }
  return { isMaster: false, isHidden: false };
}
