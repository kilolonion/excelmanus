"use client";

import { useCallback, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Copy,
  ExternalLink,
  Eye,
  EyeOff,
  Loader2,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { testModelConnection } from "@/lib/api";
import { activateModelProfile, createModelProfile, updateModelProfile } from "@/lib/model-config-api";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useUIStore } from "@/stores/ui-store";
import { PROVIDER_LOGO_SLUG } from "../../settings/model/constants";
import { requestModelSubTab } from "../../settings/model/model-subtab";
import { OAUTH_PROVIDER_IDS, type ProviderGuide } from "../provider-guides";

function ProviderLogo({ id }: { id: string }) {
  const slug = PROVIDER_LOGO_SLUG[id];
  if (!slug) return null;
  return (
    <span
      className="inline-block h-7 w-7 shrink-0"
      role="img"
      aria-label={`${id} logo`}
      style={{
        backgroundColor: "currentColor",
        maskImage: `url(/providers/${slug}.svg)`,
        WebkitMaskImage: `url(/providers/${slug}.svg)`,
        maskSize: "contain",
        WebkitMaskSize: "contain",
        maskRepeat: "no-repeat",
        WebkitMaskRepeat: "no-repeat",
        maskPosition: "center",
        WebkitMaskPosition: "center",
      }}
    />
  );
}

async function copyText(value: string): Promise<boolean> {
  if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // Sandboxed webviews may deny the Clipboard API; use the legacy fallback below.
    }
  }
  if (typeof document === "undefined") return false;
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}

interface ProviderGuideStepProps {
  provider: ProviderGuide;
  onBack: () => void;
  onComplete: () => void;
  onSkip?: () => void;
}

export function ProviderGuideStep({ provider, onBack, onComplete, onSkip }: ProviderGuideStepProps) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [model, setModel] = useState(provider.model);
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; error?: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [expandedGuide, setExpandedGuide] = useState(true);
  const [copied, setCopied] = useState(false);

  const handleTest = useCallback(async () => {
    if (!apiKey.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testModelConnection({ model, base_url: baseUrl, api_key: apiKey });
      setTestResult({ ok: result.ok, error: result.error });
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof Error ? e.message : "测试失败" });
    } finally {
      setTesting(false);
    }
  }, [apiKey, baseUrl, model]);

  const handleSave = useCallback(async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    try {
      const profileName = provider.label || model;
      const payload = {
        name: profileName,
        model,
        base_url: baseUrl,
        api_key: apiKey,
        protocol: provider.protocol,
        description: provider.description || "",
      };
      try {
        await createModelProfile(payload);
      } catch (error) {
        if (!(error instanceof Error) || !("status" in error) || error.status !== 409) throw error;
        await updateModelProfile(profileName, payload);
      }
      await activateModelProfile(profileName);
      useOnboardingStore.getState().setBackendConfigured(true);
      onComplete();
    } catch (e) {
      setTestResult({ ok: false, error: e instanceof Error ? e.message : "保存失败" });
    } finally {
      setSaving(false);
    }
  }, [apiKey, baseUrl, model, onComplete, provider.description, provider.label, provider.protocol]);

  const handleCopyUrl = useCallback(async () => {
    const didCopy = await copyText(provider.purchaseUrl);
    setCopied(didCopy);
    window.setTimeout(() => setCopied(false), 2000);
  }, [provider.purchaseUrl]);

  const handleGoOAuth = useCallback(() => {
    requestModelSubTab("subscription");
    useOnboardingStore.getState().skipAll();
    useUIStore.getState().openSettings("model");
  }, []);

  const isOAuthProvider = OAUTH_PROVIDER_IDS.has(provider.id);
  const canProceed = apiKey.trim().length > 0;

  return (
    <div className="em-onboarding-step em-onboarding-provider-guide">
      <div className="em-onboarding-guide-heading">
        <Button variant="ghost" size="sm" onClick={onBack} className="em-onboarding-back-button">
          <ArrowLeft aria-hidden="true" />
          返回供应商列表
        </Button>
        <div className="em-onboarding-provider-heading">
          <span className="em-onboarding-provider-logo is-large"><ProviderLogo id={provider.id} /></span>
          <div>
            <div className="em-onboarding-eyebrow">第 3 步 · 建立连接</div>
            <h2 tabIndex={-1}>配置 {provider.label}</h2>
            <p>{provider.description}</p>
          </div>
        </div>
      </div>

      <div className="em-onboarding-guide-layout">
        <section className="em-onboarding-guide-card" aria-labelledby="provider-guide-title">
          <button
            type="button"
            className="em-onboarding-guide-toggle"
            onClick={() => setExpandedGuide((expanded) => !expanded)}
            aria-expanded={expandedGuide}
          >
            <span>
              <span className="em-onboarding-guide-kicker">连接前准备</span>
              <strong id="provider-guide-title">{isOAuthProvider ? "如何连接订阅" : "如何获取 API Key"}</strong>
            </span>
            {expandedGuide ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
          </button>
          <AnimatePresence initial={false}>
            {expandedGuide && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="em-onboarding-guide-steps">
                  {provider.steps.map((guideStep, i) => (
                    <div key={i} className="em-onboarding-guide-step">
                      <span>{i + 1}</span>
                      <div><strong>{guideStep.title.replace(/^\d+\.\s*/, "")}</strong><p>{guideStep.description}</p></div>
                    </div>
                  ))}
                </div>
                <div className="em-onboarding-guide-links">
                  <a href={provider.purchaseUrl} target="_blank" rel="noopener noreferrer">
                    前往 {provider.label}
                    <ExternalLink aria-hidden="true" />
                  </a>
                  <button type="button" onClick={handleCopyUrl}>
                    {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
                    {copied ? "已复制" : "复制链接"}
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </section>

        <section className="em-onboarding-config-card" aria-labelledby="provider-config-title">
          <div className="em-onboarding-config-heading">
            <div>
              <span className="em-onboarding-guide-kicker">连接信息</span>
              <h3 id="provider-config-title">{isOAuthProvider ? "完成订阅授权" : "填入模型配置"}</h3>
            </div>
            <span className="em-onboarding-secure-note"><CheckCircle2 aria-hidden="true" /> 私密保存</span>
          </div>

          {isOAuthProvider ? (
            <>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {provider.label} 使用订阅账号浏览器授权连接，无需填写 API Key。点击下方按钮前往「设置 → 订阅与 OAuth」完成授权，连接成功后会自动创建模型档案。
              </p>
              <div className="em-onboarding-config-actions">
                <div className="em-onboarding-config-spacer" />
                {onSkip && <Button variant="ghost" onClick={onSkip}>跳过</Button>}
                <Button onClick={handleGoOAuth} className="em-onboarding-primary-action">
                  <ExternalLink aria-hidden="true" />
                  前往授权
                </Button>
              </div>
              <p className="em-onboarding-config-footnote">授权在设置页完成；授权成功后回到本向导或直接在模型选择器中选用。</p>
            </>
          ) : (
            <>
              <label className="em-onboarding-field">
                <span>Model ID</span>
                <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder={provider.model} autoComplete="off" spellCheck={false} />
              </label>
              <label className="em-onboarding-field">
                <span>Base URL</span>
                <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder={provider.base_url} autoComplete="url" spellCheck={false} />
              </label>
              <label className="em-onboarding-field">
                <span>API Key</span>
                <span className="em-onboarding-key-input">
                  <Input
                    value={apiKey}
                    onChange={(e) => { setApiKey(e.target.value); setTestResult(null); }}
                    type={showKey ? "text" : "password"}
                    placeholder="粘贴你的 API Key"
                    autoComplete="off"
                    spellCheck={false}
                    aria-label="API Key"
                  />
                  <button type="button" onClick={() => setShowKey((shown) => !shown)} aria-label={showKey ? "隐藏 API Key" : "显示 API Key"}>
                    {showKey ? <EyeOff aria-hidden="true" /> : <Eye aria-hidden="true" />}
                  </button>
                </span>
              </label>

              <AnimatePresence initial={false}>
                {testResult && (
                  <motion.div
                    initial={{ opacity: 0, height: 0 }}
                    animate={{ opacity: 1, height: "auto" }}
                    exit={{ opacity: 0, height: 0 }}
                    className={`em-onboarding-test-result ${testResult.ok ? "is-success" : "is-error"}`}
                    role="status"
                  >
                    {testResult.ok ? <CheckCircle2 aria-hidden="true" /> : <XCircle aria-hidden="true" />}
                    <span>{testResult.ok ? "连接成功，模型可以使用。" : testResult.error || "连接失败，请检查配置。"}</span>
                  </motion.div>
                )}
              </AnimatePresence>

              <div className="em-onboarding-config-actions">
                <Button variant="outline" onClick={handleTest} disabled={!canProceed || testing} className="em-onboarding-test-button">
                  {testing ? <Loader2 className="animate-spin" aria-hidden="true" /> : testResult?.ok ? <CheckCircle2 aria-hidden="true" /> : null}
                  测试连接
                </Button>
                <div className="em-onboarding-config-spacer" />
                {onSkip && <Button variant="ghost" onClick={onSkip}>跳过</Button>}
                <Button onClick={handleSave} disabled={!canProceed || saving} className="em-onboarding-primary-action">
                  {saving ? <Loader2 className="animate-spin" aria-hidden="true" /> : <ArrowRight aria-hidden="true" />}
                  保存并继续
                </Button>
              </div>
              <p className="em-onboarding-config-footnote">配置会保存为模型档案并立即激活，之后可在设置中添加更多模型。</p>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
