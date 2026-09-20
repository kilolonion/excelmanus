"use client";

import { useId, useState } from "react";
import { ChevronDown, ExternalLink, LockKeyhole } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  JEV_PROVIDER_PRESETS,
  draftFromJevPreset,
  jevPresetById,
  type JevProviderDraft,
  type JevProtocol,
} from "@/lib/jev-settings";

const FIELD = "h-9 text-xs rounded-lg";

const PROTOCOL_OPTIONS: { value: JevProtocol; label: string }[] = [
  { value: "typesafe", label: "typesafe（官方 System One）" },
  { value: "gateway", label: "gateway（Vercel Evaluate）" },
];

export function JevProviderForm({
  draft,
  onChange,
  configured,
}: {
  draft: JevProviderDraft;
  onChange: (next: JevProviderDraft) => void;
  configured: boolean;
}) {
  const [keyVisible, setKeyVisible] = useState(false);
  const id = useId();
  const preset = jevPresetById(draft.id);
  const protocolLabel = PROTOCOL_OPTIONS.find((option) => option.value === draft.protocol)?.label;

  return (
    <div className="rounded-xl border border-border/70 bg-background p-3 space-y-2.5">
      <div className="space-y-1">
        <label htmlFor={`${id}-name`} className="text-xs font-medium text-foreground/80">供应商名称</label>
        <Input
          id={`${id}-name`}
          className={FIELD}
          value={draft.name}
          placeholder="例如 TypeSafe"
          onChange={(event) => onChange({ ...draft, name: event.target.value })}
        />
      </div>
      <div className="space-y-1">
        <label htmlFor={`${id}-key`} className="text-xs font-medium text-foreground/80">API Key</label>
        <div className="relative">
          <Input
            id={`${id}-key`}
            aria-describedby={`${id}-key-help`}
            type={keyVisible ? "text" : "password"}
            autoComplete="off"
            spellCheck={false}
            className={`${FIELD} font-mono pr-16`}
            value={draft.api_key}
            placeholder={configured ? "留空保留现有密钥" : "粘贴密钥"}
            onChange={(event) => onChange({ ...draft, api_key: event.target.value })}
          />
          <button
            type="button"
            aria-label={keyVisible ? "隐藏密钥" : "显示密钥"}
            aria-pressed={keyVisible}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-[11px] text-muted-foreground hover:text-foreground"
            onClick={() => setKeyVisible((value) => !value)}
          >
            {keyVisible ? "隐藏" : "显示"}
          </button>
        </div>
        <p id={`${id}-key-help`} className="text-[11px] leading-5 text-muted-foreground">{configured ? "密钥已保存。仅在需要更换时输入新密钥。" : "保存密钥后，在「模型配置」中选择介入方式。"}</p>
        {preset && (
          <a
            href={preset.purchaseUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 text-[11px] hover:underline"
            style={{ color: "var(--em-primary)" }}
          >
            获取密钥 <ExternalLink className="h-2.5 w-2.5" />
          </a>
        )}
      </div>
      <details open={!preset} className="group border-t border-border/60 pt-2.5">
      <summary className="mb-3 flex cursor-pointer list-none items-center gap-1 rounded text-xs text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">连接设置{preset && " · 已按预设填写"}<ChevronDown className="ml-auto size-3.5 transition-transform group-open:rotate-180" /></summary>
      <div className="space-y-1">
        <label htmlFor={`${id}-url`} className="text-xs font-medium text-foreground/80">API 请求地址</label>
        <Input
          id={`${id}-url`}
          type="url"
          spellCheck={false}
          className={`${FIELD} font-mono`}
          value={draft.base_url}
          placeholder="https://"
          onChange={(event) => onChange({ ...draft, base_url: event.target.value })}
        />
      </div>
      <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-2.5">
        <div className="space-y-1">
          <label className="text-[11px] font-medium text-foreground/80">协议</label>
          {preset ? (
            <div
              className="flex h-9 w-full items-center gap-2 rounded-lg border border-input bg-muted/25 px-2.5 text-xs text-foreground/75"
              aria-label="协议（预设固定）"
            >
              <span className="min-w-0 flex-1 truncate">{protocolLabel}</span>
              <LockKeyhole className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70" aria-hidden />
            </div>
          ) : (
            <DropdownMenu modal={false}>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  aria-label="连接协议"
                  className="group flex h-9 w-full items-center gap-2 rounded-lg border border-input bg-background px-2.5 text-left text-xs shadow-[0_1px_2px_rgba(24,58,40,0.04)] transition-[border-color,background-color,box-shadow] hover:border-[var(--em-primary-alpha-25)] hover:bg-muted/30 focus-visible:border-[var(--em-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary-alpha-15)] data-[state=open]:border-[var(--em-primary)] data-[state=open]:bg-[var(--em-primary-alpha-06)] data-[state=open]:shadow-[0_0_0_3px_var(--em-primary-alpha-10)]"
                >
                  <span className="min-w-0 flex-1 truncate">{protocolLabel}</span>
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="start"
                sideOffset={6}
                className="w-[var(--radix-dropdown-menu-trigger-width)] rounded-xl border-border/80 bg-popover/95 p-1.5 shadow-xl backdrop-blur-sm"
              >
                <DropdownMenuRadioGroup
                  value={draft.protocol}
                  onValueChange={(value) => onChange({ ...draft, protocol: value as JevProtocol })}
                >
                  {PROTOCOL_OPTIONS.map((option) => (
                    <DropdownMenuRadioItem
                      key={option.value}
                      value={option.value}
                      className="rounded-lg py-2 pr-2.5 pl-7 text-xs transition-colors focus:bg-[var(--em-primary-alpha-10)] focus:text-foreground data-[state=checked]:bg-[var(--em-primary-alpha-06)] data-[state=checked]:font-medium data-[state=checked]:[&_svg]:!text-[var(--em-primary)]"
                    >
                      {option.label}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
        <div className="space-y-1">
          <label htmlFor={`${id}-model`} className="text-xs font-medium text-foreground/80">模型 ID</label>
          <Input
            id={`${id}-model`}
            className={`${FIELD} font-mono`}
            value={draft.model}
            placeholder={preset?.model || "jev-1.13.0"}
            onChange={(event) => onChange({ ...draft, model: event.target.value })}
          />
        </div>
      </div>
      {!preset && (
        <p className="text-[11px] text-muted-foreground">
          自定义端点请选择协议：TypeSafe 直连走 System One；Vercel 兼容口走 Evaluate。
        </p>
      )}
      </details>
    </div>
  );
}

export function JevPresetPicker({
  draft,
  onSelectPreset,
  onSelectCustom,
}: {
  draft: JevProviderDraft;
  onSelectPreset: (presetId: string) => void;
  onSelectCustom: () => void;
}) {
  const isCustom = !JEV_PROVIDER_PRESETS.some((preset) => preset.id === draft.id);
  return (
    <div className="rounded-xl border border-border/70 bg-muted/20 p-2.5 space-y-2">
      <p className="text-[11px] font-medium text-foreground/80">从预设开始</p>
      <p className="text-[11px] text-muted-foreground -mt-1">
        TypeSafe 与 Vercel 预填连接，或点「自定义」填写自己的服务
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
        {JEV_PROVIDER_PRESETS.map((preset) => {
          const selected = draft.id === preset.id;
          return (
            <button
              key={preset.id}
              type="button"
              aria-pressed={selected}
              className={`text-left rounded-xl border px-2.5 py-2 transition-all ${
                selected
                  ? "border-[var(--em-primary)]/60 bg-[var(--em-primary)]/5 shadow-[0_0_0_1px_var(--em-primary-alpha-15)]"
                  : "border-border bg-background hover:border-[var(--em-primary)]/40 hover:bg-[var(--em-primary)]/5"
              }`}
              onClick={() => onSelectPreset(preset.id)}
            >
              <span className="text-[11px] sm:text-xs font-medium truncate block">{preset.label}</span>
              <p className="text-[10px] font-mono text-muted-foreground truncate mt-0.5">{preset.model}</p>
            </button>
          );
        })}
        <button
          type="button"
          aria-pressed={isCustom}
          className={`text-left rounded-xl border px-2.5 py-2 transition-all ${
            isCustom
              ? "border-[var(--em-primary)]/60 bg-[var(--em-primary)]/5 shadow-[0_0_0_1px_var(--em-primary-alpha-15)]"
              : "border-dashed border-border bg-background hover:border-[var(--em-primary)]/40 hover:bg-[var(--em-primary)]/5"
          }`}
          onClick={onSelectCustom}
        >
          <span className="text-[11px] sm:text-xs font-medium truncate block">自定义</span>
          <p className="text-[10px] text-muted-foreground truncate mt-0.5">填写自己的服务</p>
        </button>
      </div>
    </div>
  );
}

export { draftFromJevPreset };
