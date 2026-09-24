/** Translate renderer commands into the canonical ChangeSet style contract. */
export function workbookStylePatch(value: unknown): Record<string, unknown> | null {
  if (value == null) return null;
  if (typeof value !== "object" || Array.isArray(value)) throw new Error("样式必须先展开为对象");
  const style = value as Record<string, any>;
  const out: Record<string, any> = {};
  const font: Record<string, unknown> = {};
  for (const [from, to] of [["bl", "bold"], ["it", "italic"], ["fs", "size"], ["ff", "name"]]) {
    if (from in style) font[to] = from === "bl" || from === "it" ? Boolean(style[from]) : style[from];
  }
  if ("ul" in style) font.underline = style.ul?.s ? "single" : null;
  if ("st" in style) font.strike = Boolean(style.st?.s);
  if (style.cl?.rgb) font.color = style.cl.rgb;
  if (Object.keys(font).length) out.font = font;
  if ("bg" in style) out.fill = style.bg?.rgb ? { color: style.bg.rgb } : { type: "none" };
  const alignment: Record<string, unknown> = {};
  if ("ht" in style) alignment.horizontal = ({ 1: "left", 2: "center", 3: "right", 4: "justify" } as Record<number, string>)[style.ht] ?? null;
  if ("vt" in style) alignment.vertical = ({ 1: "top", 2: "center", 3: "bottom" } as Record<number, string>)[style.vt] ?? null;
  if ("tb" in style) alignment.wrap_text = style.tb === 3;
  if ("tr" in style) alignment.text_rotation = style.tr?.a ?? 0;
  if ("pd" in style) alignment.indent = style.pd?.l ?? 0;
  if ("sk" in style) alignment.shrink_to_fit = Boolean(style.sk);
  if (Object.keys(alignment).length) out.alignment = alignment;
  if ("n" in style) out.number_format = style.n?.pattern || "General";
  if (style.bd) {
    const names: Record<number, string> = { 1: "thin", 2: "hair", 3: "dotted", 4: "dashed", 5: "dashDot", 6: "dashDotDot", 7: "double", 8: "medium", 9: "mediumDashed", 10: "mediumDashDot", 11: "mediumDashDotDot", 12: "slantDashDot", 13: "thick" };
    out.border = {};
    for (const [short, side] of [["l", "left"], ["r", "right"], ["t", "top"], ["b", "bottom"]]) {
      if (short in style.bd) out.border[side] = { style: names[style.bd[short]?.s] ?? "none", color: style.bd[short]?.cl?.rgb };
    }
  }
  return out;
}
