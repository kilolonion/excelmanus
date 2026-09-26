"use client";

import { useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { AlertCircle, ArrowRight, Check, ChevronRight, Crown, Loader2, Search, Settings2, Sparkles, X } from "lucide-react";
import { ProviderAvatar } from "@/components/settings/model/ProviderLogo";
import { requestModelSubTab, type ModelSubTab } from "@/components/settings/model/model-subtab";
import { cleanModelDescription, displayModelLabel, formatModelIdForDisplay, isSameModelReference } from "@/lib/model-display";
import { getProviderColor, getProviderDisplayName, inferModelBrand } from "@/lib/provider-brand";
import type { ModelInfo } from "@/lib/types";
import { useUIStore } from "@/stores/ui-store";
import { modelProviderId, modelProviderLabels } from "./model-provider";
import styles from "./ModelPickerContent.module.css";

export interface ModelPickerContentProps {
  models: ModelInfo[];
  currentModel: string | null;
  onSelect: (name: string) => void;
  onClose: () => void;
  capsMap?: Record<string, { healthy: boolean | null; health_error: string }>;
  switching?: boolean;
  loading?: boolean;
  loadError?: string | null;
  switchError?: string | null;
  onReload?: () => void;
  mobile?: boolean;
}

function errorMessage(error: string, switching: boolean) {
  if (/failed to fetch|network\s?error|load failed|fetch failed/i.test(error)) {
    return switching ? "连接中断，模型未切换，请重新选择" : "暂时无法连接服务，请检查网络后重试";
  }
  return error;
}

/** Shared desktop / mobile picker, with keyboard navigation scoped to model rows. */
export function ModelPickerContent({
  models, currentModel, onSelect, onClose, capsMap = {}, switching = false,
  loading = false, loadError = null, switchError = null, onReload, mobile = false,
}: ModelPickerContentProps) {
  const [search, setSearch] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const positionedOnActiveRef = useRef(false);
  const listId = useId();
  const query = search.trim().toLocaleLowerCase();
  const providerLabels = useMemo(() => modelProviderLabels(models), [models]);
  const filtered = useMemo(() => models.filter((model) => {
    const provider = modelProviderId(model);
    return [model.name, model.model, model.display_name, model.resolved_model, model.description,
      provider, providerLabels.get(provider), inferModelBrand(model)].some((value) => value?.toLocaleLowerCase().includes(query));
  }), [models, providerLabels, query]);
  const groups = new Map<string, ModelInfo[]>();
  for (const model of filtered) {
    const provider = modelProviderId(model);
    const group = groups.get(provider) || [];
    group.push(model);
    groups.set(provider, group);
  }

  // Position the list when the picker opens (or its models finish loading).
  // Later selection changes must leave the user's current scroll position alone.
  useLayoutEffect(() => {
    if (positionedOnActiveRef.current || query || !currentModel) return;
    const list = listRef.current;
    const selected = list?.querySelector<HTMLButtonElement>('[data-model-row][data-selected="true"]');
    if (!list || !selected) return;
    const listRect = list.getBoundingClientRect();
    const selectedRect = selected.getBoundingClientRect();
    list.scrollTop += selectedRect.top - listRect.top - (list.clientHeight - selectedRect.height) / 2;
    positionedOnActiveRef.current = true;
  }, [currentModel, filtered, query]);

  const navigate = (tab: ModelSubTab) => {
    onClose();
    requestModelSubTab(tab);
    useUIStore.getState().openSettings("model");
  };

  const handleArrowKey = (event: KeyboardEvent<HTMLElement>) => {
    const fromSearch = event.target === searchRef.current;
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    if (fromSearch && (event.key === "Home" || event.key === "End")) return;
    const rows = Array.from(listRef.current?.querySelectorAll<HTMLButtonElement>("[data-model-row]:not(:disabled)") || []);
    if (!rows.length) return;
    event.preventDefault();
    const index = rows.indexOf(event.target as HTMLButtonElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1
      : event.key === "ArrowDown" ? (index + 1) % rows.length
      : index <= 0 ? rows.length - 1 : index - 1;
    rows[next].focus();
    rows[next].scrollIntoView({ block: "nearest" });
  };

  const renderModel = (model: ModelInfo) => {
    const selected = model.name === currentModel;
    const brand = inferModelBrand(model);
    const label = displayModelLabel(model);
    const modelId = formatModelIdForDisplay(model.resolved_model || model.model);
    const description = cleanModelDescription(model.description, [label, model.name, modelId]);
    const detail = [isSameModelReference(modelId, label) ? "" : modelId, description].filter(Boolean).join(" · ");
    const unhealthy = capsMap[model.name]?.healthy === false;
    return (
      <button key={model.name} type="button" data-model-row="" aria-pressed={selected}
        disabled={switching} onClick={() => onSelect(model.name)}
        className={styles.model} data-selected={selected} title={[label, detail].filter(Boolean).join("\n")}>
        <ProviderAvatar id={brand} label={getProviderDisplayName(brand)} color={getProviderColor(brand)}
          className={styles.avatar} iconClassName="h-4 w-4" />
        <span className={styles.modelInfo}>
          <span className={styles.modelName}>{label}</span>
          {detail && <span className={styles.modelDetail}>{detail}</span>}
        </span>
        {unhealthy && <span className={styles.unhealthy} title={capsMap[model.name].health_error || "模型连接异常"}>
          <AlertCircle size={12} aria-hidden="true" />异常
        </span>}
        {selected && <span className={styles.selectedMark} aria-label="当前模型"><Check size={13} /></span>}
      </button>
    );
  };

  return (
    <div className={styles.panel} data-mobile={mobile}>
      <div className={styles.header}>
        <span className={styles.headingIcon}><Sparkles size={16} /></span>
        <span className={styles.heading}>选择模型</span>
        <span className={styles.count} aria-live="polite">{switching ? "切换中…" : query ? `${filtered.length} / ${models.length}` : `${models.length} 个模型`}</span>
        <button type="button" className={styles.iconButton} title="管理模型" aria-label="管理模型" onClick={() => navigate("providers")}><Settings2 size={15} /></button>
        {mobile && <button type="button" className={styles.iconButton} aria-label="关闭模型选择" onClick={onClose}><X size={16} /></button>}
      </div>

      <div className={styles.searchWrap}>
        <div className={styles.search}>
          <Search size={15} aria-hidden="true" />
          <input ref={searchRef} type="text" value={search} aria-label="搜索模型或服务商" aria-controls={listId}
            placeholder="搜索模型或服务商" onChange={(event) => setSearch(event.target.value)} onKeyDown={handleArrowKey} />
          {search && <button type="button" className={styles.clearSearch} aria-label="清空搜索" onClick={() => { setSearch(""); searchRef.current?.focus(); }}><X size={13} /></button>}
        </div>
      </div>

      <div ref={listRef} id={listId} className={styles.list} onKeyDown={handleArrowKey} aria-busy={loading || switching}>
        {Array.from(groups, ([provider, entries]) => <section key={provider} className={styles.group} aria-label={providerLabels.get(provider) || provider}>
          <div className={styles.sectionLabel}><span>{providerLabels.get(provider) || provider}</span><span className={styles.groupCount}>{entries.length}</span></div>
          {entries.map(renderModel)}
        </section>)}
        {models.length === 0 && <div className={styles.empty} role="status">
          {loading ? <Loader2 size={22} className="animate-spin" /> : loadError ? <AlertCircle size={22} /> : <Sparkles size={22} />}
          <strong>{loading ? "正在加载模型…" : loadError ? "模型列表加载失败" : "还没有配置模型"}</strong>
          <span>{loading ? "稍等片刻，即可选择" : loadError ? "恢复连接后，可重新加载列表" : "添加模型或连接订阅账号后开始使用"}</span>
          {!loading && !loadError && <button type="button" onClick={() => navigate("providers")}>添加模型<ArrowRight size={13} /></button>}
        </div>}
        {models.length > 0 && filtered.length === 0 && <div className={styles.empty} role="status">
          <Search size={22} /><strong>未找到匹配的模型</strong><span>试试其他名称或服务商</span>
          <button type="button" onClick={() => { setSearch(""); searchRef.current?.focus(); }}>清空搜索</button>
        </div>}
      </div>

      {(switchError || loadError) && <div className={styles.error} role="alert">
        <AlertCircle size={14} aria-hidden="true" />
        <span>{errorMessage((switchError || loadError)!, Boolean(switchError))}{!switchError && models.length > 0 && "，当前显示上次加载的列表"}</span>
        {!switchError && onReload && <button type="button" disabled={loading} onClick={onReload}>{loading ? "重试中…" : "重试"}</button>}
      </div>}

      <div className={styles.footer}>
        <button type="button" className={styles.subscription} onClick={() => navigate("subscription")}>
          <span className={styles.subscriptionIcon}><Crown size={17} /></span>
          <span className={styles.subscriptionCopy}><strong>连接订阅账号</strong><span>使用已有订阅，无需 API Key</span></span>
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}
