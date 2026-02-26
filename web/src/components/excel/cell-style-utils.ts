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
