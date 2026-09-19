/** 把 FastAPI / 业务错误 JSON 收成可读字符串，避免 `new Error(detail)` 变成 `[object Object]`。 */
export function formatApiErrorMessage(data: unknown, status: number): string {
  if (data == null || typeof data !== "object") {
    return `API error: ${status}`;
  }
  const payload = data as Record<string, unknown>;
  const text = stringifyApiErrorDetail(payload.error)
    || stringifyApiErrorDetail(payload.detail)
    || stringifyApiErrorDetail(payload.message);
  return text || `API error: ${status}`;
}

function stringifyApiErrorDetail(raw: unknown): string {
  if (raw == null) return "";
  if (typeof raw === "string") return raw.trim();
  if (typeof raw === "number" || typeof raw === "boolean") return String(raw);
  if (Array.isArray(raw)) {
    return raw.map(formatValidationItem).filter(Boolean).join("; ");
  }
  if (typeof raw === "object") {
    const rec = raw as Record<string, unknown>;
    if (typeof rec.message === "string" && rec.message.trim()) return rec.message.trim();
    if (typeof rec.error === "string" && rec.error.trim()) return rec.error.trim();
    if (typeof rec.msg === "string" && rec.msg.trim()) return rec.msg.trim();
  }
  return "";
}

function formatValidationItem(item: unknown): string {
  if (typeof item === "string") return item.trim();
  if (!item || typeof item !== "object") return "";
  const rec = item as Record<string, unknown>;
  const msg = typeof rec.msg === "string"
    ? rec.msg
    : typeof rec.message === "string"
      ? rec.message
      : "";
  if (!msg.trim()) return "";
  const loc = Array.isArray(rec.loc)
    ? rec.loc.filter((part) => part !== "body").join(".")
    : "";
  return loc ? `${loc}: ${msg}` : msg.trim();
}
