"use client";

export function AssistantWaitingIndicator() {
  return (
    <div
      className="assistant-wait"
      role="status"
      aria-live="polite"
      aria-label="正在生成"
    >
      <span className="assistant-wait-dot" />
      <span className="assistant-wait-dot" />
      <span className="assistant-wait-dot" />
    </div>
  );
}
