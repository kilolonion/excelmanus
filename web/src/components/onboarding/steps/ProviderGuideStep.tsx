"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  Eye,
  EyeOff,
  Loader2,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiPut, testModelConnection } from "@/lib/api";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { PROVIDER_LOGO_SLUG } from "../provider-guides";
import type { ProviderGuide } from "../provider-guides";

function ProviderLogo({ id, size = 5 }: { id: string; size?: number }) {
  const slug = PROVIDER_LOGO_SLUG[id];
  if (!slug) return null;
  return (
    <span
      className="inline-block shrink-0"
      role="img"
      aria-label={id}
      style={{
        height: `${size * 4}px`,
        width: `${size * 4}px`,
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

interface ProviderGuideStepProps {
  provider: ProviderGuide;
  isAdmin: boolean;
  onBack: () => void;
  onComplete: () => void;
  onSkip?: () => void;
}

export function ProviderGuideStep({
  provider,
  isAdmin,
  onBack,
  onComplete,
  onSkip,
}: ProviderGuideStepProps) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(provider.base_url);
  const [model, setModel] = useState(provider.model);
  const [showKey, setShowKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    ok: boolean;
    error?: string;
  } | null>(null);
  const [saving, setSaving] = useState(false);
  const [expandedGuide, setExpandedGuide] = useState(true);
  const [copied, setCopied] = useState(false);

  const handleTest = useCallback(async () => {
    if (!apiKey.trim()) return;
    setTesting(true);
    setTestResult(null);
    try {
      const result = await testModelConnection({
        model,
        base_url: baseUrl,
        api_key: apiKey,
      });
      setTestResult({ ok: result.ok, error: result.error });
    } catch (e) {
      setTestResult({
        ok: false,
        error: e instanceof Error ? e.message : "测试失败",
      });
    } finally {
      setTesting(false);
    }
  }, [apiKey, baseUrl, model]);

  const handleSave = useCallback(async () => {
    if (!apiKey.trim()) return;
    setSaving(true);
    try {
      if (isAdmin) {
        await apiPut(
          "/config/models/main",
          {
            model,
            base_url: baseUrl,
            api_key: apiKey,
            protocol: provider.protocol,
          },
          { direct: true }
        );
        // Config saved successfully — clear degraded mode flag
        useOnboardingStore.getState().setBackendConfigured(true);
      } else {
        const { updateProfile } = await import("@/lib/auth-api");
        await updateProfile({
          llm_api_key: apiKey,
          llm_base_url: baseUrl,
          llm_model: model,
        });
      }
      onComplete();
    } catch (e) {
      setTestResult({
        ok: false,
        error: e instanceof Error ? e.message : "保存失败",
      });
    } finally {
      setSaving(false);
    }
  }, [apiKey, baseUrl, model, provider.protocol, isAdmin, onComplete]);

  const handleCopyUrl = useCallback(() => {
    navigator.clipboard.writeText(provider.purchaseUrl).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [provider.purchaseUrl]);

  const canProceed = apiKey.trim().length > 0;

  return (
    <div className="flex flex-col items-center min-h-full px-4 sm:px-6 py-6 sm:py-8">
      <div className="max-w-2xl w-full">
        {/* Header */}
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          className="gap-1.5 text-muted-foreground -ml-2 mb-4"
        >
          <ArrowLeft className="h-4 w-4" />
          返回选择
        </Button>

        <div className="flex items-center gap-3 mb-4 sm:mb-6">
          <div className="w-9 sm:w-10 h-9 sm:h-10 rounded-lg bg-muted/50 flex items-center justify-center">
            <ProviderLogo id={provider.id} size={6} />
          </div>
          <div>
            <h2 className="text-lg sm:text-xl font-bold tracking-tight">
              配置 {provider.label}
            </h2>
            <p className="text-xs text-muted-foreground">
              {provider.description}
            </p>
          </div>
        </div>

        {/* Guide section — collapsible */}
        <div className="rounded-xl border border-border/60 bg-muted/30 mb-4 sm:mb-6 overflow-hidden">
          <button
            type="button"
            onClick={() => setExpandedGuide(!expandedGuide)}
            className="w-full flex items-center gap-2 px-4 py-3 text-left hover:bg-muted/50 transition-colors"
          >
            {expandedGuide ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
            <span className="text-sm font-semibold">
              如何获取 {provider.label} API Key
            </span>
          </button>

          <AnimatePresence>
            {expandedGuide && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="px-4 pb-4 space-y-3">
                  {provider.steps.map((guideStep, i) => (
                    <div
                      key={i}
                      className="flex gap-3 p-3 rounded-lg bg-background/60 border border-border/40"
                    >
                      <div
                        className="flex-shrink-0 w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold text-white"
                        style={{ backgroundColor: "var(--em-primary)" }}
                      >
                        {i + 1}
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium mb-0.5">
                          {guideStep.title}
                        </p>
                        <p className="text-xs text-muted-foreground leading-relaxed">
                          {guideStep.description}
                        </p>
                      </div>
                    </div>
                  ))}

                  {/* Purchase URL button */}
                  <div className="flex items-center gap-2 pt-1">
                    <a
                      href={provider.purchaseUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90"
                      style={{ backgroundColor: "var(--em-primary)" }}
                    >
                      前往 {provider.label}
                      <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                    <button
                      type="button"
                      onClick={handleCopyUrl}
                      className="inline-flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs text-muted-foreground hover:text-foreground border border-border/60 hover:bg-muted/50 transition-colors"
                    >
                      {copied ? (
                        <Check className="h-3 w-3 text-green-500" />
                      ) : (
                        <Copy className="h-3 w-3" />
                      )}
                      {copied ? "已复制" : "复制链接"}
                    </button>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* API Key input section */}
        <div className="rounded-xl border border-border/60 bg-background/80 p-4 sm:p-5 space-y-3 sm:space-y-4">
          <h3 className="text-sm font-semibold">填入你的 API 配置</h3>

          {/* Model */}
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Model ID</label>
            <Input
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="h-9 text-sm font-mono"
              placeholder={provider.model}
            />
          </div>

          {/* Base URL */}
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">Base URL</label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              className="h-9 text-sm font-mono"
              placeholder={provider.base_url}
            />
          </div>

          {/* API Key */}
          <div className="space-y-1.5">
            <label className="text-xs text-muted-foreground">API Key</label>
            <div className="relative">
              <Input
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setTestResult(null);
                }}
                type={showKey ? "text" : "password"}
                className="h-9 text-sm font-mono pr-9"
                placeholder="粘贴你的 API Key..."
                autoFocus
              />
              <button
                type="button"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setShowKey(!showKey)}
              >
                {showKey ? (
                  <EyeOff className="h-3.5 w-3.5" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </button>
            </div>
          </div>

          {/* Test result */}
          <AnimatePresence>
            {testResult && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
                  testResult.ok
                    ? "bg-green-50 dark:bg-green-950/30 text-green-700 dark:text-green-400 border border-green-200 dark:border-green-800/50"
                    : "bg-red-50 dark:bg-red-950/30 text-red-700 dark:text-red-400 border border-red-200 dark:border-red-800/50"
                }`}
              >
                {testResult.ok ? (
                  <CheckCircle2 className="h-4 w-4 mt-0.5 flex-shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
                )}
                <span className="text-xs leading-relaxed">
                  {testResult.ok
                    ? "连接成功！模型可用。"
                    : testResult.error || "连接失败，请检查配置"}
                </span>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Action buttons */}
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handleTest}
              disabled={!canProceed || testing}
              className="h-9 gap-1.5"
            >
              {testing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : testResult?.ok ? (
                <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
              ) : (
                <span className="w-2 h-2 rounded-full bg-muted-foreground/40" />
              )}
              测试连通性
            </Button>

            <div className="flex-1" />

            <div className="flex gap-2">
              {onSkip && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onSkip}
                  className="h-9 text-muted-foreground"
                >
                  跳过
                </Button>
              )}
              <Button
                size="sm"
                onClick={handleSave}
                disabled={!canProceed || saving}
                className="h-9 gap-1.5 text-white"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                {saving ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <ArrowRight className="h-3.5 w-3.5" />
                )}
                保存并继续
              </Button>
            </div>
          </div>
        </div>

        {/* Note for admin */}
        {isAdmin && (
          <p className="text-[11px] text-muted-foreground mt-4 text-center">
            此配置将作为系统主模型。你可以稍后在设置中配置辅助模型和视觉模型。
          </p>
        )}
      </div>
    </div>
  );
}
