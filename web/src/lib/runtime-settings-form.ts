import type { ReactNode } from "react";
import type { SettingValue } from "@/stores/settings-draft-store";

export type RuntimeSettingValue = SettingValue;
export type RuntimeSettings = Record<string, SettingValue>;
export type SettingEffect = "immediate" | "next-turn" | "new-session" | "restart";

export const SETTING_EFFECT_LABELS: Record<SettingEffect, string> = {
  immediate: "保存后立即生效",
  "next-turn": "下一条消息生效",
  "new-session": "新对话生效",
  restart: "保存后重启服务",
};

export interface RuntimeSettingItem {
  key: string;
  label: string;
  desc: string;
  icon?: ReactNode;
  coachId?: string;
  type: "bool" | "int" | "float" | "select" | "string";
  options?: { value: string; label: string }[];
  min?: number;
  max?: number;
  exclusiveMin?: number;
  exclusiveMax?: number;
  step?: number | "any";
  unit?: string;
  placeholder?: string;
  effect?: SettingEffect;
  /** Zero clears a manual override; the API reports the effective value separately. */
  automatic?: { label: string; effectiveKey: string };
  disabledWhen?: (settings: RuntimeSettings) => boolean;
  disabledDesc?: string;
  validate?: (value: SettingValue, settings: RuntimeSettings) => string | undefined;
}

export interface RuntimeSettingGroup {
  title: string;
  description?: string;
  icon: ReactNode;
  items: RuntimeSettingItem[];
  defaultOpen?: boolean;
  category?: string;
}

export function settingError(item: RuntimeSettingItem, value: SettingValue, settings: RuntimeSettings): string | undefined {
  if (item.type === "int" || item.type === "float") {
    if (typeof value === "boolean" || String(value).trim() === "" || !Number.isFinite(Number(value))) return "请输入有效数字";
    const number = Number(value);
    if (item.automatic && number === 0) return;
    if (item.type === "int" && !Number.isInteger(number)) return "请输入整数";
    if (item.exclusiveMin !== undefined && number <= item.exclusiveMin) return `必须大于 ${item.exclusiveMin}`;
    if (item.exclusiveMax !== undefined && number >= item.exclusiveMax) return `必须小于 ${item.exclusiveMax}`;
    if (item.min !== undefined && number < item.min) return `不能小于 ${item.min}`;
    if (item.max !== undefined && number > item.max) return `不能大于 ${item.max}`;
  }
  if (item.type === "select" && !item.options?.some((option) => option.value === value)) return "请选择有效选项";
  return item.validate?.(value, settings);
}

export function settingPayload(items: RuntimeSettingItem[], draft: RuntimeSettings): RuntimeSettings {
  return Object.fromEntries(items.filter((item) => Object.hasOwn(draft, item.key)).map((item) => [
    item.key,
    item.type === "int" || item.type === "float" ? Number(draft[item.key]) : draft[item.key],
  ]));
}
