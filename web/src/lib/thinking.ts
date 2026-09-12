export function formatThinkingDuration(seconds: number): string {
  if (seconds <= 0) return "";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const r = seconds % 60;
  return r > 0 ? `${m}m ${r}s` : `${m}m`;
}

export function thinkingPreview(content: string, max = 48): string {
  const oneLine = content.replace(/\s+/g, " ").trim();
  if (!oneLine) return "";
  return oneLine.length > max ? `${oneLine.slice(0, max)}…` : oneLine;
}
