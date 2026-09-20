"use client";

import React, { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Terminal,
  AtSign,
  ChevronRight,
  Sparkles,
  Cpu,
} from "lucide-react";
import type { PopoverMode } from "./chat-input-constants";

export interface PopoverItem {
  command: string;
  description: string;
  icon: React.ReactNode;
  isActive?: boolean;
  hasChildren?: boolean;
}

interface CommandPopoverProps {
  popover: PopoverMode;
  popoverItems: PopoverItem[];
  selectedIndex: number;
  setSelectedIndex: (i: number) => void;
  selectPopoverItem: (item: PopoverItem) => void;
  popoverRef: React.RefObject<HTMLDivElement | null>;
  activeSlashCmd: string | null;
  atCategory: string | null;
  onBackToSlash: () => void;
  onBackToAt: () => void;
}

export function CommandPopover({
  popover,
  popoverItems,
  selectedIndex,
  setSelectedIndex,
  selectPopoverItem,
  popoverRef,
  activeSlashCmd,
  atCategory,
  onBackToSlash,
  onBackToAt,
}: CommandPopoverProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtual = popoverItems.length > 100;
  const virtualizer = useVirtualizer({
    count: popoverItems.length,
    getScrollElement: () => scrollRef.current,
    getItemKey: (index) => popoverItems[index].command || index,
    estimateSize: () => 40,
    overscan: 4,
    enabled: virtual && !!popover,
  });
  useEffect(() => {
    if (virtual && popover && popoverItems.length) virtualizer.scrollToIndex(selectedIndex, { align: "auto" });
  }, [selectedIndex, popover, popoverItems.length, virtual, virtualizer]);
  if (!popover || popoverItems.length === 0) return null;
  const rows = virtual ? virtualizer.getVirtualItems() : popoverItems.map((_, index) => ({ index, start: 0 }));

  return (
    <div
      ref={popoverRef}
      data-coach-id="coach-command-popover"
      className="absolute bottom-full left-0 right-0 mb-2 bg-card border border-border/60 rounded-2xl shadow-xl overflow-hidden z-50"
    >
      <div className="px-3.5 py-2 text-[11px] text-muted-foreground border-b border-border/40 flex items-center gap-1.5">
        {popover === "at" && <><AtSign className="h-3 w-3" /> 提及</>}
        {popover === "at-sub" && (
          <>
            <button
              className="touch-compact hover:text-foreground transition-colors"
              onClick={onBackToAt}
            >
              <AtSign className="h-3 w-3" />
            </button>
            <ChevronRight className="h-2.5 w-2.5" />
            <span className="font-medium text-foreground">{atCategory}</span>
          </>
        )}
        {(popover === "slash" || popover === "slash-args") && <><Terminal className="h-3 w-3" /> 命令</>}
        {popover === "slash-args" && activeSlashCmd && (
          <>
            <ChevronRight className="h-2.5 w-2.5" />
            <span className="font-medium text-foreground">{activeSlashCmd}</span>
          </>
        )}
        {popover === "slash-skills" && (
          <>
            <button
              className="touch-compact hover:text-foreground transition-colors"
              onClick={onBackToSlash}
            >
              <Terminal className="h-3 w-3" />
            </button>
            <ChevronRight className="h-2.5 w-2.5" />
            <Sparkles className="h-3 w-3" />
            <span className="font-medium text-foreground">选择技能</span>
          </>
        )}
        {popover === "slash-model" && (
          <>
            <button
              className="touch-compact hover:text-foreground transition-colors"
              onClick={onBackToSlash}
            >
              <Terminal className="h-3 w-3" />
            </button>
            <ChevronRight className="h-2.5 w-2.5" />
            <Cpu className="h-3 w-3" />
            <span className="font-medium text-foreground">切换模型</span>
          </>
        )}
        <span className="ml-auto text-[10px] opacity-60 hidden sm:inline">↑↓ 导航 · Tab 选择 · Esc 关闭</span>
      </div>
      <div ref={scrollRef} className="max-h-[min(192px,40dvh)] sm:max-h-60 overflow-y-auto py-1 overscroll-contain">
        <div style={virtual ? { height: virtualizer.getTotalSize(), position: "relative" } : undefined}>
        {rows.map((row) => {
          const i = row.index;
          const item = popoverItems[i];
          const isActive = item.isActive;
          const hasChildren = item.hasChildren;
          // 主斜杠菜单中为 /skills 和 /model 显示下钻箭头
          const isDrillable = popover === "slash" && (item.command === "/skills" || item.command === "/model");
          return (
            <button
              key={item.command || `empty-${i}`}
              data-index={i}
              ref={virtual ? virtualizer.measureElement : undefined}
              style={virtual ? { position: "absolute", top: 0, left: 0, transform: `translateY(${row.start}px)` } : undefined}
              className={`w-full flex items-center gap-2.5 px-3.5 py-2 text-sm text-left transition-colors ${
                item.command ? (i === selectedIndex ? "bg-[var(--em-primary-alpha-10)]" : "hover:bg-accent/40") : "opacity-50 cursor-default"
              }`}
              onPointerEnter={() => item.command && setSelectedIndex(i)}
              onClick={() => item.command && selectPopoverItem(item)}
            >
              <span className="text-muted-foreground flex-shrink-0">
                {item.icon}
              </span>
              <span
                className={`font-mono text-xs flex-1 truncate ${isActive ? "font-semibold" : ""}`}
                style={{ color: item.command ? "var(--em-primary)" : undefined }}
              >
                {item.command || item.description}
              </span>
              {item.command && (
                <span className="text-xs text-muted-foreground truncate">
                  {item.description}
                </span>
              )}
              {(hasChildren || isDrillable) && (
                <ChevronRight className="h-3 w-3 text-muted-foreground flex-shrink-0" />
              )}
            </button>
          );
        })}
        </div>
      </div>
    </div>
  );
}
