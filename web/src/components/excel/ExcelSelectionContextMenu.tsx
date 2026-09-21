"use client";

import { BarChart3, ClipboardPlus, Eraser, FileSearch, Sigma, X } from "lucide-react";
import {
  SELECTION_AGENT_ACTIONS,
  type RibbonAskContext,
  type SelectionAgentKind,
} from "@/lib/excel-ribbon-actions";

export interface ExcelSelectionContextMenuState extends RibbonAskContext {
  x: number;
  y: number;
}

interface ExcelSelectionContextMenuProps {
  state: ExcelSelectionContextMenuState;
  onClose: () => void;
  onReference: () => void;
  onAsk: (kind: SelectionAgentKind) => void;
}

function actionIcon(kind: SelectionAgentKind) {
  if (kind === "data-quality-selection") return <BarChart3 className="h-3.5 w-3.5" />;
  if (kind === "explain-selection") return <Sigma className="h-3.5 w-3.5" />;
  if (kind === "clean-selection") return <Eraser className="h-3.5 w-3.5" />;
  return <FileSearch className="h-3.5 w-3.5" />;
}

export function ExcelSelectionContextMenu({
  state,
  onClose,
  onReference,
  onAsk,
}: ExcelSelectionContextMenuProps) {
  return (
    <div
      role="menu"
      aria-label="选区 Agent 操作"
      data-em-selection-context-menu
      className="absolute z-50 min-w-[230px] overflow-hidden rounded-lg border border-border bg-background/95 p-1.5 text-sm shadow-xl backdrop-blur"
      style={{ left: state.x, top: state.y }}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.preventDefault()}
    >
      <div className="px-2 py-1.5 text-[11px] text-muted-foreground">
        <div className="font-medium text-foreground">{state.sheet} · {state.range}</div>
        <div>把这个选区交给对话中的 Agent</div>
      </div>
      <div className="my-1 border-t border-border/70" />
      <button
        type="button"
        role="menuitem"
        data-em-selection-action="reference"
        className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left hover:bg-muted"
        onClick={() => { onReference(); onClose(); }}
      >
        <ClipboardPlus className="h-3.5 w-3.5 text-[var(--em-primary)]" />
        <span className="flex-1">添加到对话框</span>
        <span className="text-[10px] text-muted-foreground">@选区</span>
      </button>
      {SELECTION_AGENT_ACTIONS.map((action) => (
        <button
          key={action.kind}
          type="button"
          role="menuitem"
          data-em-selection-action={action.kind}
          title={action.title}
          className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left hover:bg-muted"
          onClick={() => { onAsk(action.kind); onClose(); }}
        >
          {actionIcon(action.kind)}
          <span>{action.label}</span>
        </button>
      ))}
      <div className="my-1 border-t border-border/70" />
      <button
        type="button"
        role="menuitem"
        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-muted-foreground hover:bg-muted hover:text-foreground"
        onClick={onClose}
      >
        <X className="h-3.5 w-3.5" />
        关闭
      </button>
    </div>
  );
}
