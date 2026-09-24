"use client";

import { useCallback, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { Popover } from "radix-ui";
import { Input } from "@/components/ui/input";
import { glassMenuPanelClass } from "@/components/ui/menu-panel";
import type { RemoteModelItem } from "@/lib/api";
import { cn } from "@/lib/utils";
import { filterDetectedModels } from "./helpers";

export function ModelIdCombobox({
  value, models, usedModelIds, open, onOpenChange, onChange, onSelect, disabled,
}: {
  value: string;
  models: RemoteModelItem[];
  usedModelIds: Set<string>;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChange: (value: string) => void;
  onSelect: (model: RemoteModelItem) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const anchorRef = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [activeId, setActiveId] = useState<string | null>(null);
  const filtered = useMemo(() => filterDetectedModels(models, query), [models, query]);
  const expanded = open && models.length > 0 && !disabled;
  const activeIndex = filtered.findIndex((model) => model.id === (activeId ?? value));
  const activeOptionId = expanded && activeIndex >= 0 ? `${id}-option-${activeIndex}` : undefined;

  const changeOpen = (next: boolean) => {
    if (next) {
      // Browsing always starts with the full list, even for a custom Model ID.
      setQuery("");
      setActiveId(value);
    }
    onOpenChange(next);
  };

  const select = (model: RemoteModelItem) => {
    onSelect(model);
    onOpenChange(false);
    inputRef.current?.focus({ preventScroll: true });
  };

  const scrollToActive = useCallback((list: HTMLDivElement | null) => {
    if (!list || !activeOptionId) return;
    // Scroll only the options viewport, never the surrounding settings form.
    // The portal mounts later; observe its final height after collision handling.
    const reveal = () => {
      const option = document.getElementById(activeOptionId);
      if (!option) return;
      const top = option.offsetTop;
      const bottom = top + option.offsetHeight;
      if (top < list.scrollTop) list.scrollTop = top;
      else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
    };
    reveal();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(reveal);
    observer?.observe(list);
    return () => observer?.disconnect();
  }, [activeOptionId]);

  return (
    <Popover.Root open={expanded} onOpenChange={changeOpen}>
      <Popover.Anchor asChild>
        <div ref={anchorRef} className="relative flex-1 min-w-0">
          <Input
            ref={inputRef}
            role="combobox"
            aria-label="Model ID"
            aria-autocomplete="list"
            aria-expanded={expanded}
            aria-controls={expanded ? `${id}-listbox` : undefined}
            aria-activedescendant={activeOptionId}
            autoComplete="off"
            spellCheck={false}
            disabled={disabled}
            value={value}
            placeholder={models.length ? "输入搜索或选择模型" : "例如：claude-sonnet-4"}
            className={cn("h-9 text-xs rounded-lg font-mono", models.length > 0 && "pr-9")}
            onClick={() => { if (!expanded && models.length) changeOpen(true); }}
            onChange={(event) => {
              const next = event.target.value;
              onChange(next);
              setQuery(next);
              setActiveId(null);
              if (models.length) onOpenChange(true);
            }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing || event.keyCode === 229) return;
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                if (!models.length) return;
                event.preventDefault();
                if (!expanded) {
                  changeOpen(true);
                  setActiveId(models.find((model) => model.id === value)?.id
                    ?? models[event.key === "ArrowDown" ? 0 : models.length - 1].id);
                } else if (filtered.length) {
                  const next = activeIndex < 0
                    ? (event.key === "ArrowDown" ? 0 : filtered.length - 1)
                    : (activeIndex + (event.key === "ArrowDown" ? 1 : -1) + filtered.length) % filtered.length;
                  setActiveId(filtered[next].id);
                }
              } else if (event.key === "Enter" && expanded) {
                event.preventDefault();
                if (activeIndex >= 0) select(filtered[activeIndex]);
                else onOpenChange(false); // Keep a manually entered ID intact.
              } else if (event.key === "Escape" && expanded) {
                event.preventDefault();
                event.stopPropagation();
                onOpenChange(false);
              } else if (event.key === "Tab") {
                onOpenChange(false);
              }
            }}
          />
          {models.length > 0 && (
            <button
              type="button"
              aria-label={expanded ? "收起模型列表" : "展开模型列表"}
              aria-expanded={expanded}
              aria-controls={expanded ? `${id}-listbox` : undefined}
              disabled={disabled}
              tabIndex={-1}
              className="absolute right-0.5 top-0.5 flex h-8 w-8 !min-h-0 !min-w-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted/60 hover:text-foreground"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                changeOpen(!expanded);
                inputRef.current?.focus({ preventScroll: true });
              }}
            >
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform duration-150 motion-reduce:transition-none", expanded && "rotate-180")} />
            </button>
          )}
        </div>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={12}
          className={cn(glassMenuPanelClass, "z-[80] flex min-w-0 flex-col border w-[var(--radix-popover-trigger-width)] max-h-[var(--radix-popover-content-available-height)] outline-none")}
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onInteractOutside={(event) => {
            if (event.target instanceof Node && anchorRef.current?.contains(event.target)) event.preventDefault();
          }}
        >
          <div className="flex shrink-0 items-center justify-between gap-2 px-2 py-1.5 text-[11px] text-muted-foreground">
            <span role="status" aria-live="polite">
              {query.trim() ? `${filtered.length} / ${models.length} 个匹配` : `检测到 ${models.length} 个模型`}
            </span>
            <span aria-hidden="true" className="hidden sm:inline text-[10px]">↑↓ 选择 · Enter 确认</span>
          </div>
          <div ref={scrollToActive} id={`${id}-listbox`} role="listbox" aria-label="可用模型" className="relative min-h-0 max-h-60 overflow-y-auto overscroll-contain">
            {filtered.map((model, index) => (
              <div
                key={model.id}
                id={`${id}-option-${index}`}
                role="option"
                aria-selected={value === model.id}
                title={model.id}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2.5 py-2 text-xs font-mono",
                  value === model.id && "text-[var(--em-primary)]",
                  activeIndex === index ? "bg-[var(--em-primary-alpha-10)]" : "hover:bg-muted/60",
                )}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => select(model)}
              >
                <span className="min-w-0 truncate">{model.id}</span>
                <span className="flex max-w-[45%] shrink-0 items-center gap-1.5 text-[10px] font-sans text-muted-foreground">
                  {usedModelIds.has(model.id) && <span className="shrink-0">已添加</span>}
                  {model.owned_by && <span className="truncate">{model.owned_by}</span>}
                  <span className="w-3.5 shrink-0">{value === model.id && <Check className="h-3.5 w-3.5 text-[var(--em-primary)]" />}</span>
                </span>
              </div>
            ))}
            {filtered.length === 0 && <p className="px-2.5 py-3 text-[11px] text-muted-foreground">无匹配模型，可保留手动输入的 Model ID。</p>}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
