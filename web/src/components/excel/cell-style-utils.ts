/**
 * 将 Univer 兼容的 CellStyle 对象转为 React CSSProperties。
 * 供 ExcelDiffTable / ExcelPreviewTable 等组件共用。
 */

import type { CellStyle } from "@/stores/excel-store";
import type { CSSProperties } from "react";

const H_ALIGN: Record<number, CSSProperties["textAlign"]> = {
  0: "left",
  1: "center",
  2: "right",
  3: "justify",
};

const V_ALIGN: Record<number, CSSProperties["verticalAlign"]> = {
  0: "top",
  1: "middle",
  2: "bottom",
};

const BORDER_STYLE: Record<number, string> = {
  1: "1px solid",
  2: "2px solid",
  3: "3px solid",
  4: "1px dashed",
  5: "1px dotted",
  6: "3px double",
  7: "1px solid",   // hair
  8: "2px dashed",
  9: "1px dashed",
  10: "2px dashed",
  11: "1px dashed",
  12: "2px dashed",
  13: "2px dashed",
};

/**
 * 将 CellStyle 转为 CSSProperties。
 * 返回空对象 `{}` 如果 style 为 null/undefined。
 */
export function cellStyleToCSS(style: CellStyle | null | undefined): CSSProperties {
  if (!style) return {};
  const css: CSSProperties = {};

  // font
  if (style.bl) css.fontWeight = "bold";
  if (style.it) css.fontStyle = "italic";
  if (style.ul?.s) css.textDecoration = "underline";
  if (style.st?.s) css.textDecoration = (css.textDecoration ? css.textDecoration + " line-through" : "line-through");
  if (style.fs) css.fontSize = `${style.fs}px`;
  if (style.ff) css.fontFamily = style.ff;
  if (style.cl?.rgb) css.color = style.cl.rgb;

  // background
  if (style.bg?.rgb) css.backgroundColor = style.bg.rgb;

  // alignment
  if (style.ht != null) css.textAlign = H_ALIGN[style.ht];
  if (style.vt != null) css.verticalAlign = V_ALIGN[style.vt];
  if (style.tb) {
    css.whiteSpace = "pre-wrap";
    css.wordBreak = "break-word";
    css.overflow = "visible";
  }

  // text rotation
  if (style.tr?.a) {
    const deg = style.tr.a;
    if (deg === 255) {
      // 255 = vertical stacked text in Excel
      css.writingMode = "vertical-lr";
      css.textOrientation = "upright";
    } else if (deg <= 90) {
      css.transform = `rotate(-${deg}deg)`;
    } else if (deg <= 180) {
      css.transform = `rotate(${180 - deg}deg)`;
    }
  }

  // indent (padding)
  if (style.pd?.l && style.pd.l > 0) {
    css.paddingLeft = `${style.pd.l * 12}px`;
  }

  // shrink to fit
  if (style.sk) {
    css.overflow = "hidden";
    css.textOverflow = "ellipsis";
  }

  // borders
  if (style.bd) {
    for (const [key, val] of Object.entries(style.bd)) {
      const bs = BORDER_STYLE[val.s] ?? "1px solid";
      const color = val.cl?.rgb ?? "#000";
      const value = `${bs} ${color}`;
      if (key === "l") css.borderLeft = value;
      else if (key === "r") css.borderRight = value;
      else if (key === "t") css.borderTop = value;
      else if (key === "b") css.borderBottom = value;
    }
  }

  return css;
}

/**
 * 检查 CellStyle 是否启用了自动换行。
 * 供组件决定是否移除 truncate / whitespace-nowrap 类。
 */
export function hasWrapText(style: CellStyle | null | undefined): boolean {
  return !!style?.tb;
}

/**
 * 根据 Excel 数字格式模式对原始值进行简易格式化。
 * 仅处理最常见的模式（百分比、千分位、货币、固定小数）。
 * 不匹配时返回 null，由调用方 fallback 到默认 String(value)。
 */
export function formatCellByPattern(
  value: string | number | null,
  style: CellStyle | null | undefined,
): string | null {
  if (value == null || style?.n?.pattern == null) return null;
  const pattern = style.n.pattern;
  const num = typeof value === "number" ? value : parseFloat(value);
  if (isNaN(num)) return null;

  // 百分比: 0%, 0.0%, 0.00% 等
  if (pattern.includes("%")) {
    const decimals = (pattern.match(/0\.(0+)%/) || [])[1]?.length ?? 0;
    return `${(num * 100).toFixed(decimals)}%`;
  }

  // 货币: ¥#,##0.00 / $#,##0.00 / €#,##0.00 等
  const currencyMatch = pattern.match(/^([¥$€£₩])\s*#/);
  if (currencyMatch) {
    const symbol = currencyMatch[1];
    const decimals = (pattern.match(/\.(0+)/) || [])[1]?.length ?? 0;
    return `${symbol}${num.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
  }

  // 千分位: #,##0 / #,##0.00
  if (pattern.includes("#,##0")) {
    const decimals = (pattern.match(/\.(0+)/) || [])[1]?.length ?? 0;
    return num.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  }

  // 固定小数: 0.00 / 0.0
  const fixedMatch = pattern.match(/^0\.(0+)$/);
  if (fixedMatch) {
    return num.toFixed(fixedMatch[1].length);
  }

  return null;
}
