"use client";

import { useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Loader2,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
  Radio,
  Terminal,
  BookOpen,
  Zap,
  FolderOpen,
  Shield,
  Link2,
  MessageSquare,
  Settings2,
  Info,
  Play,
  Square,
  FlaskConical,
  Save,
  Eye,
  EyeOff,
  AlertCircle,
  CheckCircle2,
  RotateCw,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  fetchChannelsStatus,
  fetchServerPublicIp,
  saveChannelConfig,
  startChannel,
  stopChannel,
  testChannelConfig,
  updateChannelSettings,
  updateRateLimitSettings,
  type ChannelStatusInfo,
  type ChannelDetail,
  type ChannelFieldDef,
  type RateLimitConfig,
  type ChannelSettings,
} from "@/lib/api";
import { ChannelIcon, CHANNEL_META } from "@/components/ui/ChannelIcons";

// ── Collapsible Section ───────────────────────────────────

function Section({
  title,
  icon: Icon,
  defaultOpen = false,
  children,
  badge,
}: {
  title: string;
  icon: typeof Radio;
  defaultOpen?: boolean;
  children: React.ReactNode;
  badge?: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <div
        role="button"
        tabIndex={0}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setOpen(!open); } }}
        className="flex items-center gap-2 w-full px-4 py-3 text-left hover:bg-muted/40 transition-colors cursor-pointer"
      >
        <Icon className="h-4 w-4 shrink-0" style={{ color: "var(--em-primary)" }} />
        <span className="text-sm font-medium flex-1">{title}</span>
        {badge}
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 pt-1 border-t border-border/50">
              {children}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Copy Button ───────────────────────────────────────────

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [text]);
  return (
    <button
      onClick={handleCopy}
      className="h-6 w-6 inline-flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors shrink-0 cursor-pointer"
      title="复制"
    >
      {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
    </button>
  );
}

// ── Code Block ────────────────────────────────────────────

function CodeBlock({ children }: { children: string }) {
  return (
    <div className="relative group">
      <pre className="text-xs font-mono bg-muted/60 rounded-md px-3 py-2 overflow-x-auto whitespace-pre-wrap break-all">
        {children}
      </pre>
      <div className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 transition-opacity">
        <CopyButton text={children} />
      </div>
    </div>
  );
}

// ── Toast Notification ────────────────────────────────────

function Toast({
  message,
  type,
  onClose,
}: {
  message: string;
  type: "success" | "error" | "info";
  onClose: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [onClose]);

  const colors = {
    success: "bg-green-500/15 text-green-700 dark:text-green-400 border-green-500/30",
    error: "bg-red-500/15 text-red-700 dark:text-red-400 border-red-500/30",
    info: "bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30",
  };
  const icons = {
    success: <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />,
    error: <AlertCircle className="h-3.5 w-3.5 shrink-0" />,
    info: <Info className="h-3.5 w-3.5 shrink-0" />,
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: -10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className={`flex items-center gap-2 px-3 py-2 rounded-md border text-xs ${colors[type]}`}
    >
      {icons[type]}
      <span className="flex-1">{message}</span>
      <button onClick={onClose} className="text-current opacity-60 hover:opacity-100 cursor-pointer">×</button>
    </motion.div>
  );
}

// ── Status Badge ──────────────────────────────────────────

function StatusBadge({ status }: { status: string }) {
  if (status === "running") {
    return (
      <span className="flex items-center gap-1 text-[10px] text-green-600 dark:text-green-400 font-medium">
        <span className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse" />
        运行中
      </span>
    );
  }
  if (status === "error") {
    return (
      <span className="flex items-center gap-1 text-[10px] text-red-600 dark:text-red-400 font-medium">
        <span className="h-1.5 w-1.5 rounded-full bg-red-500" />
        异常
      </span>
    );
  }
  return (
    <Badge variant="secondary" className="text-[10px] px-2 py-0.5 border-0 bg-muted text-muted-foreground shrink-0">
      未启动
    </Badge>
  );
}

// ── QQ Bot IP Whitelist Guide ─────────────────────────────

function QQWhitelistGuide() {
  const [ip, setIp] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchServerPublicIp()
      .then((res) => setIp(res.ip))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-md bg-blue-500/10 border border-blue-500/20 text-blue-600 dark:text-blue-400">
      <Info className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0 space-y-1">
        <p className="text-xs font-medium">IP 白名单配置</p>
        <p className="text-[11px] leading-relaxed opacity-80">
          QQ Bot 要求将服务器的公网 IP 添加到开放平台白名单，否则会报
          <code className="px-1 py-0.5 rounded bg-blue-500/10 font-mono text-[10px] mx-0.5">接口访问源IP不在白名单</code>
          错误。
        </p>
        <p className="text-[11px] leading-relaxed opacity-80">
          当前服务器公网 IP：{loading ? (
            <Loader2 className="inline h-3 w-3 animate-spin align-text-bottom" />
          ) : ip ? (
            <span className="inline-flex items-center gap-1">
              <code className="px-1.5 py-0.5 rounded bg-blue-500/10 font-mono text-[10px] font-semibold">{ip}</code>
              <CopyButton text={ip} />
            </span>
          ) : (
            <span className="text-[10px] opacity-60">检测失败</span>
          )}
        </p>
        <p className="text-[11px] leading-relaxed opacity-70">
          前往{" "}
          <a
            href="https://q.qq.com/#/app/bot-appid/setting/dev"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 font-medium hover:opacity-100"
          >
            QQ 开放平台 → 开发设置
          </a>
          {" "}→ IP 白名单，添加上述 IP。
        </p>
      </div>
    </div>
  );
}

// ── Feishu Webhook Guide ──────────────────────────────────

function FeishuWebhookGuide() {
  const webhookUrl = typeof window !== "undefined"
    ? `${window.location.origin}/api/v1/channels/feishu/webhook`
    : "/api/v1/channels/feishu/webhook";

  return (
    <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-md bg-blue-500/10 border border-blue-500/20 text-blue-600 dark:text-blue-400">
      <Info className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0 space-y-1">
        <p className="text-xs font-medium">Webhook 配置</p>
        <p className="text-[11px] leading-relaxed opacity-80">
          飞书 Bot 通过 Webhook 接收消息。请在飞书开放平台的「事件订阅」中配置以下请求地址：
        </p>
        <div className="relative group">
          <code className="block text-[11px] font-mono bg-blue-500/10 rounded px-2.5 py-1.5 break-all">
            {webhookUrl}
          </code>
          <div className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity">
            <CopyButton text={webhookUrl} />
          </div>
        </div>
        <p className="text-[11px] leading-relaxed opacity-70">
          前往{" "}
          <a
            href="https://open.feishu.cn/app"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 font-medium hover:opacity-100"
          >
            飞书开放平台 → 应用管理
          </a>
          {" "}→ 事件订阅 → 请求地址，填入上述 URL 并订阅
          {" "}<code className="px-1 py-0.5 rounded bg-blue-500/10 font-mono text-[10px]">im.message.receive_v1</code>
          {" "}事件。
        </p>
      </div>
    </div>
  );
}

// ── Channel Config Card ───────────────────────────────────

function ChannelConfigCard({
  detail,
  onRefresh,
  onToast,
}: {
  detail: ChannelDetail;
  onRefresh: () => void;
  onToast: (msg: string, type: "success" | "error" | "info") => void;
}) {
  const [open, setOpen] = useState(false);
  const [creds, setCreds] = useState<Record<string, string>>(detail.credentials || {});
  const [enabled, setEnabled] = useState(detail.enabled);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [starting, setStarting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({});
  const [dirty, setDirty] = useState(false);

  const meta = CHANNEL_META[detail.name] || { label: detail.name, color: "#666", description: detail.name };

  // Reset form when detail changes from parent refresh
  useEffect(() => {
    setCreds(detail.credentials || {});
    setEnabled(detail.enabled);
    setDirty(false);
  }, [detail]);

  const handleFieldChange = (key: string, value: string) => {
    setCreds((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
  };

  const handleEnabledChange = (val: boolean) => {
    setEnabled(val);
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await saveChannelConfig(detail.name, creds, enabled);
      if (res.missing_fields && res.missing_fields.length > 0) {
        onToast(`已保存，但缺少必填项: ${res.missing_fields.join(", ")}`, "info");
      } else {
        onToast(`${meta.label} 配置已保存`, "success");
      }
      setDirty(false);
      onRefresh();
    } catch (e: unknown) {
      onToast(`保存失败: ${e instanceof Error ? e.message : String(e)}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const res = await testChannelConfig(detail.name, creds);
      onToast(res.message, "success");
    } catch (e: unknown) {
      onToast(`测试失败: ${e instanceof Error ? e.message : String(e)}`, "error");
    } finally {
      setTesting(false);
    }
  };

  const handleStart = async () => {
    // Auto-save before starting if dirty
    if (dirty) {
      setSaving(true);
      try {
        await saveChannelConfig(detail.name, creds, true);
        setDirty(false);
        onRefresh();
      } catch (e: unknown) {
        onToast(`保存失败: ${e instanceof Error ? e.message : String(e)}`, "error");
        setSaving(false);
        return;
      }
      setSaving(false);
    }
    setStarting(true);
    try {
      const res = await startChannel(detail.name);
      onToast(res.message, "success");
      onRefresh();
    } catch (e: unknown) {
      onToast(`启动失败: ${e instanceof Error ? e.message : String(e)}`, "error");
    } finally {
      setStarting(false);
    }
  };

  const handleStop = async () => {
    setStopping(true);
    try {
      const res = await stopChannel(detail.name);
      onToast(res.message, "success");
      onRefresh();
    } catch (e: unknown) {
      onToast(`停止失败: ${e instanceof Error ? e.message : String(e)}`, "error");
    } finally {
      setStopping(false);
    }
  };

  const isRunning = detail.status === "running";

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      {/* Header */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-3 w-full px-4 py-3 text-left hover:bg-muted/40 transition-colors cursor-pointer"
      >
        <div
          className="h-9 w-9 rounded-lg flex items-center justify-center text-white shrink-0"
          style={{ backgroundColor: isRunning ? meta.color : "#9ca3af" }}
        >
          <ChannelIcon channel={detail.name} className="h-4.5 w-4.5" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium">{meta.label}</span>
            {detail.dep_installed === false ? (
              <span className="flex items-center gap-1 text-[10px] text-red-600 dark:text-red-400 font-medium">
                <AlertCircle className="h-3 w-3" />
                缺少依赖
              </span>
            ) : (
              <StatusBadge status={detail.status} />
            )}
            {dirty && (
              <span className="text-[10px] text-amber-500 font-medium">未保存</span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{meta.description}</p>
        </div>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
        )}
      </button>

      {/* Config Panel */}
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 pt-2 border-t border-border/50 space-y-4">
              {/* Auto-start toggle */}
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-xs font-medium">随服务自动启动</p>
                  <p className="text-[11px] text-muted-foreground">保存配置后，服务重启时自动启动此渠道</p>
                </div>
                <button
                  type="button"
                  onClick={() => handleEnabledChange(!enabled)}
                  className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer ${
                    enabled ? "bg-green-500" : "bg-muted-foreground/30"
                  }`}
                >
                  <span
                    className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                      enabled ? "translate-x-4.5" : "translate-x-0.5"
                    }`}
                  />
                </button>
              </div>

              {/* Credential Fields */}
              <div className="space-y-3">
                <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
                  <Settings2 className="h-3 w-3" />
                  凭证配置
                </p>
                {detail.fields.map((field: ChannelFieldDef) => {
                  if (field.type === "boolean") {
                    const checked = (creds[field.key] || "").toLowerCase() === "true";
                    return (
                      <div key={field.key} className="flex items-center justify-between py-1">
                        <div>
                          <label className="text-xs font-medium">{field.label}</label>
                          <p className="text-[10px] text-muted-foreground">{field.hint}</p>
                        </div>
                        <button
                          type="button"
                          onClick={() => handleFieldChange(field.key, checked ? "false" : "true")}
                          className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer ${
                            checked ? "bg-green-500" : "bg-muted-foreground/30"
                          }`}
                        >
                          <span
                            className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                              checked ? "translate-x-4.5" : "translate-x-0.5"
                            }`}
                          />
                        </button>
                      </div>
                    );
                  }

                  const isSecret = field.secret;
                  const showThis = showSecrets[field.key] || false;

                  return (
                    <div key={field.key}>
                      <div className="flex items-center gap-1.5 mb-1">
                        <label className="text-xs font-medium">{field.label}</label>
                        {field.required && (
                          <span className="text-[9px] text-red-500 font-medium">必填</span>
                        )}
                      </div>
                      <div className="relative">
                        <input
                          type={isSecret && !showThis ? "password" : "text"}
                          value={creds[field.key] || ""}
                          onChange={(e) => handleFieldChange(field.key, e.target.value)}
                          placeholder={field.hint}
                          className="w-full text-xs px-3 py-2 rounded-md border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring pr-8"
                        />
                        {isSecret && (
                          <button
                            type="button"
                            onClick={() =>
                              setShowSecrets((prev) => ({ ...prev, [field.key]: !showThis }))
                            }
                            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground cursor-pointer"
                          >
                            {showThis ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Dependency not installed warning */}
              {detail.dep_installed === false && (
                <div className="flex items-start gap-2.5 px-3 py-2.5 rounded-md bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400">
                  <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0 space-y-1.5">
                    <p className="text-xs font-medium">Python 依赖未安装</p>
                    <p className="text-[11px] leading-relaxed opacity-80">
                      此渠道需要安装额外的 Python 包才能使用。请在服务器终端中执行以下命令，然后刷新页面：
                    </p>
                    {detail.install_hint && (
                      <div className="relative group">
                        <code className="block text-[11px] font-mono bg-red-500/10 rounded px-2.5 py-1.5 break-all">
                          {detail.install_hint}
                        </code>
                        <div className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-opacity">
                          <CopyButton text={detail.install_hint} />
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* QQ Bot IP whitelist guidance */}
              {detail.name === "qq" && <QQWhitelistGuide />}

              {/* Feishu webhook URL guidance */}
              {detail.name === "feishu" && <FeishuWebhookGuide />}

              {/* Missing fields warning — computed from local creds to avoid stale detail */}
              {(() => {
                const localMissing = detail.fields
                  .filter((f: ChannelFieldDef) => f.required && !creds[f.key])
                  .map((f: ChannelFieldDef) => f.key);
                return localMissing.length > 0 ? (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-amber-500/10 text-amber-600 dark:text-amber-400">
                    <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                    <span className="text-xs">缺少必填项: {localMissing.join(", ")}</span>
                  </div>
                ) : null;
              })()}

              {/* Action Buttons */}
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  type="button"
                  onClick={handleSave}
                  disabled={saving || !dirty}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    backgroundColor: dirty ? "var(--em-primary)" : undefined,
                    color: dirty ? "white" : undefined,
                  }}
                >
                  {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
                  保存配置
                </button>

                <button
                  type="button"
                  onClick={handleTest}
                  disabled={testing}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-muted hover:bg-muted/80 transition-colors cursor-pointer disabled:opacity-50"
                >
                  {testing ? <Loader2 className="h-3 w-3 animate-spin" /> : <FlaskConical className="h-3 w-3" />}
                  测试凭证
                </button>

                <div className="flex-1" />

                {isRunning ? (
                  <button
                    type="button"
                    onClick={handleStop}
                    disabled={stopping}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-red-500/15 text-red-600 dark:text-red-400 hover:bg-red-500/25 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {stopping ? <Loader2 className="h-3 w-3 animate-spin" /> : <Square className="h-3 w-3" />}
                    停止
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={handleStart}
                    disabled={starting || saving || detail.dep_installed === false}
                    title={detail.dep_installed === false ? "请先安装 Python 依赖" : undefined}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-green-500/15 text-green-600 dark:text-green-400 hover:bg-green-500/25 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {starting || saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />}
                    启动
                  </button>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ── Bot Command Reference ─────────────────────────────────

interface CommandInfo {
  name: string;
  desc: string;
  usage?: string;
}

interface CommandGroup {
  title: string;
  icon: typeof MessageSquare;
  commands: CommandInfo[];
}

const COMMAND_GROUPS: CommandGroup[] = [
  {
    title: "基础",
    icon: MessageSquare,
    commands: [
      { name: "/start", desc: "初始化 Bot，显示欢迎消息" },
      { name: "/help", desc: "查看所有可用命令和使用说明" },
      { name: "/new", desc: "新建对话，清除当前会话历史" },
    ],
  },
  {
    title: "模式控制",
    icon: Settings2,
    commands: [
      { name: "/mode", desc: "查看/切换聊天模式", usage: "/mode [write|read|plan]" },
      { name: "/model", desc: "查看/切换当前 LLM 模型", usage: "/model [模型名]" },
      { name: "/addmodel", desc: "添加自定义模型配置", usage: "/addmodel <名称> <provider> <model_id> <api_key>" },
      { name: "/delmodel", desc: "删除自定义模型", usage: "/delmodel <模型名>" },
      { name: "/concurrency", desc: "查看/切换并发模式", usage: "/concurrency [queue|steer|guide]" },
    ],
  },
  {
    title: "会话管理",
    icon: FolderOpen,
    commands: [
      { name: "/sessions", desc: "列出历史会话 / 切换会话", usage: "/sessions [编号]" },
      { name: "/history", desc: "查看当前会话轮次摘要" },
      { name: "/rollback", desc: "回退到指定用户轮次", usage: "/rollback <轮次号>" },
      { name: "/undo", desc: "撤销最近一次可撤销操作" },
      { name: "/abort", desc: "终止当前正在执行的任务" },
    ],
  },
  {
    title: "文件操作",
    icon: FolderOpen,
    commands: [
      { name: "/staged", desc: "查看待确认的 staged 文件列表" },
      { name: "/apply", desc: "确认应用文件变更", usage: "/apply [编号|all]" },
      { name: "/discard", desc: "丢弃文件变更", usage: "/discard [编号|all]" },
      { name: "/undoapply", desc: "撤销最近一次 apply 操作" },
    ],
  },
  {
    title: "配额与绑定",
    icon: Shield,
    commands: [
      { name: "/quota", desc: "查看 token 用量和配额" },
      { name: "/bind", desc: "生成 6 位绑定码，在 Web 前端绑定账号" },
      { name: "/bindstatus", desc: "查询当前渠道账号的绑定状态" },
      { name: "/unbind", desc: "解除当前渠道账号的绑定" },
    ],
  },
];

function CommandReference() {
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

  return (
    <div className="space-y-2">
      {COMMAND_GROUPS.map((group) => {
        const isOpen = expandedGroup === group.title;
        return (
          <div key={group.title} className="rounded-md border border-border/60 overflow-hidden">
            <button
              type="button"
              onClick={() => setExpandedGroup(isOpen ? null : group.title)}
              className="flex items-center gap-2 w-full px-3 py-2 text-left hover:bg-muted/40 transition-colors cursor-pointer"
            >
              <group.icon className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
              <span className="text-xs font-medium flex-1">{group.title}</span>
              <Badge variant="secondary" className="text-[9px] px-1.5 py-0 border-0 bg-muted text-muted-foreground">
                {group.commands.length}
              </Badge>
              {isOpen ? (
                <ChevronDown className="h-3 w-3 text-muted-foreground" />
              ) : (
                <ChevronRight className="h-3 w-3 text-muted-foreground" />
              )}
            </button>
            <AnimatePresence initial={false}>
              {isOpen && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.15, ease: "easeOut" }}
                  className="overflow-hidden"
                >
                  <div className="border-t border-border/40">
                    {group.commands.map((cmd, i) => (
                      <div
                        key={cmd.name}
                        className={`flex items-start gap-2.5 px-3 py-2 ${
                          i < group.commands.length - 1 ? "border-b border-border/20" : ""
                        }`}
                      >
                        <code
                          className="text-[11px] font-mono font-semibold px-1.5 py-0.5 rounded shrink-0 whitespace-nowrap"
                          style={{
                            backgroundColor: "var(--em-primary-alpha-10)",
                            color: "var(--em-primary)",
                          }}
                        >
                          {cmd.name}
                        </code>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs text-foreground leading-relaxed">{cmd.desc}</p>
                          {cmd.usage && (
                            <p className="text-[10px] text-muted-foreground font-mono mt-0.5">
                              {cmd.usage}
                            </p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        );
      })}
    </div>
  );
}

// ── Concurrency Modes Info ────────────────────────────────

function ConcurrencyModesInfo() {
  const modes = [
    {
      name: "queue",
      label: "排队模式",
      desc: "新消息排队等待，FIFO 串行执行。适合需要保证执行顺序的场景。",
      color: "bg-blue-500/15 text-blue-600 dark:text-blue-400",
      default: true,
    },
    {
      name: "steer",
      label: "转向模式",
      desc: "中断正在执行的旧任务，立即处理新消息。适合快速迭代修改。",
      color: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
      default: false,
    },
    {
      name: "guide",
      label: "引导模式",
      desc: "消息作为系统上下文注入运行中的 agent，不打断工具执行。适合补充说明。",
      color: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
      default: false,
    },
  ];

  return (
    <div className="space-y-2 mt-3">
      <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        <Zap className="h-3 w-3" />
        并发模式
      </p>
      <div className="grid gap-2">
        {modes.map((mode) => (
          <div key={mode.name} className="flex items-start gap-2.5 p-2.5 rounded-md border border-border/50 bg-muted/20">
            <Badge
              variant="secondary"
              className={`text-[10px] px-2 py-0.5 border-0 shrink-0 mt-0.5 ${mode.color}`}
            >
              {mode.name}
            </Badge>
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium">
                {mode.label}
                {mode.default && (
                  <span className="text-[10px] text-muted-foreground ml-1.5">(默认)</span>
                )}
              </p>
              <p className="text-[11px] text-muted-foreground mt-0.5 leading-relaxed">{mode.desc}</p>
            </div>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-muted-foreground mt-1">
        在 Bot 中使用 <code className="px-1 py-0.5 rounded bg-muted font-mono">/concurrency [模式名]</code> 切换
      </p>
    </div>
  );
}

// ── Rate Limit Info ───────────────────────────────────────

interface RateLimitFieldDef {
  key: keyof RateLimitConfig;
  label: string;
  group: string;
}

const RATE_LIMIT_FIELDS: RateLimitFieldDef[] = [
  { key: "chat_per_minute", label: "每分钟", group: "对话" },
  { key: "chat_per_hour", label: "每小时", group: "对话" },
  { key: "command_per_minute", label: "每分钟", group: "命令" },
  { key: "command_per_hour", label: "每小时", group: "命令" },
  { key: "upload_per_minute", label: "每分钟", group: "文件上传" },
  { key: "upload_per_hour", label: "每小时", group: "文件上传" },
  { key: "global_per_minute", label: "每分钟", group: "全局" },
  { key: "global_per_hour", label: "每小时", group: "全局" },
];

const RATE_LIMIT_ADVANCED_FIELDS: { key: keyof RateLimitConfig; label: string; hint: string; unit: string }[] = [
  { key: "reject_cooldown_seconds", label: "拒绝消息冷却", hint: "非白名单用户被拒后的冷却秒数", unit: "秒" },
  { key: "auto_ban_threshold", label: "自动封禁阈值", hint: "连续超限多少次触发自动封禁", unit: "次" },
  { key: "auto_ban_duration_seconds", label: "自动封禁时长", hint: "自动封禁持续多少秒", unit: "秒" },
];

function RateLimitSettings({
  config,
  envOverrides,
  onToast,
  onRefresh,
}: {
  config: RateLimitConfig;
  envOverrides: Record<string, string>;
  onToast: (msg: string, type: "success" | "error" | "info") => void;
  onRefresh: () => void;
}) {
  const [values, setValues] = useState<RateLimitConfig>(config);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  useEffect(() => {
    setValues(config);
    setDirty(false);
  }, [config]);

  const handleChange = (key: keyof RateLimitConfig, raw: string) => {
    const num = Number(raw);
    if (isNaN(num) || num < 0) return;
    setValues((prev) => ({ ...prev, [key]: num }));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const res = await updateRateLimitSettings(values);
      if (res.locked_fields && res.locked_fields.length > 0) {
        onToast(res.message || `部分字段被环境变量锁定: ${res.locked_fields.join(", ")}`, "info");
      } else {
        onToast("速率限制配置已保存", "success");
      }
      setDirty(false);
      onRefresh();
    } catch (e: unknown) {
      onToast(`保存失败: ${e instanceof Error ? e.message : String(e)}`, "error");
    } finally {
      setSaving(false);
    }
  };

  const handleReset = () => {
    setValues(config);
    setDirty(false);
  };

  const isLocked = (key: string) => key in envOverrides;

  // Group fields by category for table display
  const groups = ["对话", "命令", "文件上传", "全局"];

  return (
    <div className="space-y-2 mt-3">
      <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        <Shield className="h-3 w-3" />
        速率限制
        {dirty && <span className="text-[10px] text-amber-500 font-medium ml-1">未保存</span>}
      </p>

      {/* Main rate limit table */}
      <div className="rounded-md border border-border/50 overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/40">
              <th className="text-left px-3 py-1.5 font-medium text-muted-foreground">类别</th>
              <th className="text-right px-3 py-1.5 font-medium text-muted-foreground">每分钟</th>
              <th className="text-right px-3 py-1.5 font-medium text-muted-foreground">每小时</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group, gi) => {
              const fields = RATE_LIMIT_FIELDS.filter((f) => f.group === group);
              const pmField = fields.find((f) => f.label === "每分钟");
              const phField = fields.find((f) => f.label === "每小时");
              return (
                <tr key={group} className={gi < groups.length - 1 ? "border-b border-border/30" : ""}>
                  <td className="px-3 py-1.5 font-medium">{group}</td>
                  {[pmField, phField].map((field) => (
                    <td key={field?.key} className="px-3 py-1 text-right">
                      {field && (
                        <div className="inline-flex items-center gap-1">
                          {isLocked(field.key) && (
                            <span className="text-[9px] text-amber-500" title={`环境变量 ${envOverrides[field.key]} 锁定`}>🔒</span>
                          )}
                          <input
                            type="number"
                            min={0}
                            value={values[field.key]}
                            onChange={(e) => handleChange(field.key, e.target.value)}
                            disabled={isLocked(field.key)}
                            className="w-14 text-xs text-right tabular-nums px-1.5 py-0.5 rounded border border-border/60 bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
                          />
                        </div>
                      )}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Advanced settings toggle */}
      <button
        type="button"
        onClick={() => setShowAdvanced(!showAdvanced)}
        className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
      >
        {showAdvanced ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        封禁与冷却设置
      </button>

      <AnimatePresence initial={false}>
        {showAdvanced && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            className="overflow-hidden"
          >
            <div className="space-y-2.5 pt-1">
              {RATE_LIMIT_ADVANCED_FIELDS.map((field) => (
                <div key={field.key} className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium flex items-center gap-1">
                      {field.label}
                      {isLocked(field.key) && (
                        <span className="text-[9px] text-amber-500" title={`环境变量 ${envOverrides[field.key]} 锁定`}>🔒</span>
                      )}
                    </p>
                    <p className="text-[10px] text-muted-foreground">{field.hint}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <input
                      type="number"
                      min={0}
                      value={values[field.key]}
                      onChange={(e) => handleChange(field.key, e.target.value)}
                      disabled={isLocked(field.key)}
                      className="w-16 text-xs text-right tabular-nums px-1.5 py-1 rounded border border-border/60 bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
                    />
                    <span className="text-[10px] text-muted-foreground w-4">{field.unit}</span>
                  </div>
                </div>
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Save/Reset buttons */}
      {dirty && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-white transition-colors cursor-pointer disabled:opacity-50"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            保存
          </button>
          <button
            type="button"
            onClick={handleReset}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-muted hover:bg-muted/80 transition-colors cursor-pointer disabled:opacity-50"
          >
            <RotateCw className="h-3 w-3" />
            重置
          </button>
        </div>
      )}

      {/* Env override hint */}
      {Object.keys(envOverrides).length > 0 && (
        <p className="text-[10px] text-amber-600 dark:text-amber-400 flex items-center gap-1">
          <AlertCircle className="h-3 w-3 shrink-0" />
          🔒 标记的字段被环境变量锁定，需在服务端修改
        </p>
      )}
      <p className="text-[10px] text-muted-foreground">
        修改后立即对运行中的渠道 Bot 生效，无需重启。
      </p>
    </div>
  );
}

// ── Bind Flow Overview ────────────────────────────────────

function BindFlowOverview() {
  const steps = [
    { num: "1", label: "Bot 中 /bind", desc: "在 Bot 对话中发送 /bind 命令获取 6 位绑定码" },
    { num: "2", label: "Web 输入码", desc: '在"个人中心 > 渠道绑定"中输入绑定码' },
    { num: "3", label: "确认绑定", desc: "预览信息无误后点击确认，即可跨渠道共享会话和工作区" },
  ];

  return (
    <div className="space-y-2 mt-3">
      <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
        <Link2 className="h-3 w-3" />
        账号绑定流程
      </p>
      <div className="flex items-start gap-0">
        {steps.map((step, i) => (
          <div key={step.num} className="flex-1 min-w-0 flex flex-col items-center text-center relative">
            {/* Connector line */}
            {i < steps.length - 1 && (
              <div
                className="absolute top-3 left-[calc(50%+14px)] right-[calc(-50%+14px)] h-px bg-border"
              />
            )}
            <div
              className="h-6 w-6 rounded-full flex items-center justify-center text-xs font-bold text-white relative z-10 shrink-0"
              style={{ backgroundColor: "var(--em-primary)" }}
            >
              {step.num}
            </div>
            <p className="text-[11px] font-medium mt-1.5 leading-tight">{step.label}</p>
            <p className="text-[10px] text-muted-foreground mt-0.5 leading-snug px-1">{step.desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Tag Input (comma-separated values) ────────────────────

function TagInput({
  value,
  onChange,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="w-full text-xs px-3 py-2 rounded-md border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
    />
  );
}

// ── Select Input ──────────────────────────────────────────

function SelectInput({
  value,
  onChange,
  options,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      className="text-xs px-2 py-1.5 rounded-md border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>{opt.label}</option>
      ))}
    </select>
  );
}

// ── Access Control Settings ───────────────────────────────

function AccessControlSettings({
  settings,
  envOverrides,
  onSave,
}: {
  settings: ChannelSettings;
  envOverrides: Record<string, string>;
  onSave: (patch: Partial<ChannelSettings>) => Promise<void>;
}) {
  const [values, setValues] = useState({
    admin_users: settings.admin_users,
    group_policy: settings.group_policy,
    group_whitelist: settings.group_whitelist,
    group_blacklist: settings.group_blacklist,
    allowed_users: settings.allowed_users,
  });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValues({
      admin_users: settings.admin_users,
      group_policy: settings.group_policy,
      group_whitelist: settings.group_whitelist,
      group_blacklist: settings.group_blacklist,
      allowed_users: settings.allowed_users,
    });
    setDirty(false);
  }, [settings]);

  const update = (key: string, val: string) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(values);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  };

  const isLocked = (key: string) => key in envOverrides;

  const GROUP_POLICY_OPTIONS = [
    { value: "auto", label: "自动（绑定模式=禁止，否则=允许）" },
    { value: "deny", label: "禁止 — 仅支持私聊" },
    { value: "allow", label: "允许 — 所有群聊可用" },
    { value: "whitelist", label: "白名单 — 仅指定群可用" },
    { value: "blacklist", label: "黑名单 — 排除指定群" },
  ];

  return (
    <div className="space-y-4">
      {/* 管理员用户 */}
      <div>
        <div className="flex items-center gap-1.5 mb-1">
          <label className="text-xs font-medium">管理员用户</label>
          {isLocked("admin_users") && (
            <span className="text-[9px] text-amber-500" title={`环境变量 ${envOverrides.admin_users} 锁定`}>🔒</span>
          )}
        </div>
        <TagInput
          value={values.admin_users}
          onChange={(v) => update("admin_users", v)}
          placeholder="用户ID，多个用逗号分隔"
          disabled={isLocked("admin_users")}
        />
        <p className="text-[10px] text-muted-foreground mt-0.5">管理员始终放行，不受限流和准入策略限制</p>
      </div>

      {/* 允许用户列表 */}
      <div>
        <div className="flex items-center gap-1.5 mb-1">
          <label className="text-xs font-medium">允许用户列表</label>
        </div>
        <TagInput
          value={values.allowed_users}
          onChange={(v) => update("allowed_users", v)}
          placeholder='JSON 数组，如 ["user1","user2"]，留空=不限制'
        />
        <p className="text-[10px] text-muted-foreground mt-0.5">限制可使用 Bot 的用户，留空表示所有人可用</p>
      </div>

      {/* 群聊策略 */}
      <div>
        <div className="flex items-center gap-1.5 mb-1">
          <label className="text-xs font-medium">群聊准入策略</label>
          {isLocked("group_policy") && (
            <span className="text-[9px] text-amber-500" title={`环境变量 ${envOverrides.group_policy} 锁定`}>🔒</span>
          )}
        </div>
        <SelectInput
          value={values.group_policy}
          onChange={(v) => update("group_policy", v)}
          options={GROUP_POLICY_OPTIONS}
          disabled={isLocked("group_policy")}
        />
      </div>

      {/* 群白名单 — 仅 whitelist 模式显示 */}
      {values.group_policy === "whitelist" && (
        <div>
          <label className="text-xs font-medium mb-1 block">群聊白名单</label>
          <TagInput
            value={values.group_whitelist}
            onChange={(v) => update("group_whitelist", v)}
            placeholder='JSON 数组，如 ["chat_id_1","chat_id_2"]'
          />
          <p className="text-[10px] text-muted-foreground mt-0.5">仅这些群可使用 Bot</p>
        </div>
      )}

      {/* 群黑名单 — 仅 blacklist 模式显示 */}
      {values.group_policy === "blacklist" && (
        <div>
          <label className="text-xs font-medium mb-1 block">群聊黑名单</label>
          <TagInput
            value={values.group_blacklist}
            onChange={(v) => update("group_blacklist", v)}
            placeholder='JSON 数组，如 ["chat_id_1","chat_id_2"]'
          />
          <p className="text-[10px] text-muted-foreground mt-0.5">这些群将被禁止使用 Bot</p>
        </div>
      )}

      {/* 保存 */}
      {dirty && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-white transition-colors cursor-pointer disabled:opacity-50"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            保存
          </button>
          <button
            type="button"
            onClick={() => {
              setValues({
                admin_users: settings.admin_users,
                group_policy: settings.group_policy,
                group_whitelist: settings.group_whitelist,
                group_blacklist: settings.group_blacklist,
                allowed_users: settings.allowed_users,
              });
              setDirty(false);
            }}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-muted hover:bg-muted/80 transition-colors cursor-pointer disabled:opacity-50"
          >
            <RotateCw className="h-3 w-3" />
            重置
          </button>
        </div>
      )}

      <p className="text-[10px] text-muted-foreground">
        访问控制修改后对运行中的渠道 Bot 立即生效，无需重启。
      </p>
    </div>
  );
}

// ── Behavior Settings ─────────────────────────────────────

function BehaviorSettings({
  settings,
  envOverrides,
  onSave,
}: {
  settings: ChannelSettings;
  envOverrides: Record<string, string>;
  onSave: (patch: Partial<ChannelSettings>) => Promise<void>;
}) {
  const [values, setValues] = useState({
    default_concurrency: settings.default_concurrency,
    default_chat_mode: settings.default_chat_mode,
    public_url: settings.public_url,
  });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValues({
      default_concurrency: settings.default_concurrency,
      default_chat_mode: settings.default_chat_mode,
      public_url: settings.public_url,
    });
    setDirty(false);
  }, [settings]);

  const update = (key: string, val: string) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(values);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  };

  const isLocked = (key: string) => key in envOverrides;

  const CONCURRENCY_OPTIONS = [
    { value: "queue", label: "⏳ 排队 — FIFO 串行执行" },
    { value: "steer", label: "🔄 转向 — 中断旧任务" },
    { value: "guide", label: "📨 引导 — 注入上下文" },
  ];

  const CHAT_MODE_OPTIONS = [
    { value: "write", label: "✏️ 写入 — 可读写执行" },
    { value: "read", label: "🔍 读取 — 只读分析" },
    { value: "plan", label: "📋 计划 — 规划不执行" },
  ];

  return (
    <div className="space-y-4">
      {/* 默认并发模式 */}
      <div>
        <div className="flex items-center gap-1.5 mb-1">
          <label className="text-xs font-medium">默认并发模式</label>
          {isLocked("default_concurrency") && (
            <span className="text-[9px] text-amber-500" title={`环境变量 ${envOverrides.default_concurrency} 锁定`}>🔒</span>
          )}
        </div>
        <SelectInput
          value={values.default_concurrency}
          onChange={(v) => update("default_concurrency", v)}
          options={CONCURRENCY_OPTIONS}
          disabled={isLocked("default_concurrency")}
        />
        <p className="text-[10px] text-muted-foreground mt-0.5">新用户的默认并发模式，用户可通过 /concurrency 命令切换</p>
      </div>

      {/* 默认聊天模式 */}
      <div>
        <div className="flex items-center gap-1.5 mb-1">
          <label className="text-xs font-medium">默认聊天模式</label>
          {isLocked("default_chat_mode") && (
            <span className="text-[9px] text-amber-500" title={`环境变量 ${envOverrides.default_chat_mode} 锁定`}>🔒</span>
          )}
        </div>
        <SelectInput
          value={values.default_chat_mode}
          onChange={(v) => update("default_chat_mode", v)}
          options={CHAT_MODE_OPTIONS}
          disabled={isLocked("default_chat_mode")}
        />
        <p className="text-[10px] text-muted-foreground mt-0.5">新会话的默认聊天模式，用户可通过 /mode 命令切换</p>
      </div>

      {/* 公开访问 URL */}
      <div>
        <div className="flex items-center gap-1.5 mb-1">
          <label className="text-xs font-medium">公开访问 URL</label>
          {isLocked("public_url") && (
            <span className="text-[9px] text-amber-500" title={`环境变量 ${envOverrides.public_url} 锁定`}>🔒</span>
          )}
        </div>
        <input
          type="text"
          value={values.public_url}
          onChange={(e) => update("public_url", e.target.value)}
          placeholder="如 https://example.com（用于 Bot 生成文件下载链接）"
          disabled={isLocked("public_url")}
          className="w-full text-xs px-3 py-2 rounded-md border border-border bg-background focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50 disabled:cursor-not-allowed"
        />
        <p className="text-[10px] text-muted-foreground mt-0.5">Bot 向用户发送文件下载链接时使用的外部访问地址</p>
      </div>

      {/* 保存 */}
      {dirty && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-white transition-colors cursor-pointer disabled:opacity-50"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            保存
          </button>
          <button
            type="button"
            onClick={() => {
              setValues({
                default_concurrency: settings.default_concurrency,
                default_chat_mode: settings.default_chat_mode,
                public_url: settings.public_url,
              });
              setDirty(false);
            }}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-muted hover:bg-muted/80 transition-colors cursor-pointer disabled:opacity-50"
          >
            <RotateCw className="h-3 w-3" />
            重置
          </button>
        </div>
      )}
    </div>
  );
}

// ── Output Tuning Settings ────────────────────────────────

function OutputTuningSettings({
  settings,
  onSave,
}: {
  settings: ChannelSettings;
  onSave: (patch: Partial<ChannelSettings>) => Promise<void>;
}) {
  const [values, setValues] = useState({
    tg_edit_interval_min: settings.tg_edit_interval_min,
    tg_edit_interval_max: settings.tg_edit_interval_max,
    qq_progressive_chars: settings.qq_progressive_chars,
    qq_progressive_interval: settings.qq_progressive_interval,
    feishu_update_interval: settings.feishu_update_interval,
  });
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setValues({
      tg_edit_interval_min: settings.tg_edit_interval_min,
      tg_edit_interval_max: settings.tg_edit_interval_max,
      qq_progressive_chars: settings.qq_progressive_chars,
      qq_progressive_interval: settings.qq_progressive_interval,
      feishu_update_interval: settings.feishu_update_interval,
    });
    setDirty(false);
  }, [settings]);

  const update = (key: string, val: string) => {
    setValues((prev) => ({ ...prev, [key]: val }));
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(values);
      setDirty(false);
    } finally {
      setSaving(false);
    }
  };

  const FIELDS: { key: string; label: string; hint: string; unit: string; group: string }[] = [
    { key: "tg_edit_interval_min", label: "编辑间隔（最小）", hint: "前几次编辑的最小间隔", unit: "秒", group: "Telegram" },
    { key: "tg_edit_interval_max", label: "编辑间隔（最大）", hint: "稳定后的最大间隔", unit: "秒", group: "Telegram" },
    { key: "qq_progressive_chars", label: "渐进发送阈值", hint: "累积多少字符后发送一段", unit: "字符", group: "QQ" },
    { key: "qq_progressive_interval", label: "渐进发送间隔", hint: "两次发送的最小间隔", unit: "秒", group: "QQ" },
    { key: "feishu_update_interval", label: "卡片更新间隔", hint: "飞书消息卡片的刷新间隔", unit: "秒", group: "飞书" },
  ];

  const groups = ["Telegram", "QQ", "飞书"];

  return (
    <div className="space-y-4">
      {groups.map((group) => {
        const groupFields = FIELDS.filter((f) => f.group === group);
        return (
          <div key={group}>
            <p className="text-[11px] font-medium text-muted-foreground mb-2">{group}</p>
            <div className="space-y-2.5">
              {groupFields.map((field) => (
                <div key={field.key} className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-xs font-medium">{field.label}</p>
                    <p className="text-[10px] text-muted-foreground">{field.hint}</p>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <input
                      type="number"
                      step={field.unit === "字符" ? 10 : 0.1}
                      min={field.unit === "字符" ? 50 : 0.1}
                      value={values[field.key as keyof typeof values]}
                      onChange={(e) => update(field.key, e.target.value)}
                      className="w-16 text-xs text-right tabular-nums px-1.5 py-1 rounded border border-border/60 bg-background focus:outline-none focus:ring-1 focus:ring-ring"
                    />
                    <span className="text-[10px] text-muted-foreground w-6">{field.unit}</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        );
      })}

      {/* 保存 */}
      {dirty && (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium text-white transition-colors cursor-pointer disabled:opacity-50"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            保存
          </button>
          <button
            type="button"
            onClick={() => {
              setValues({
                tg_edit_interval_min: settings.tg_edit_interval_min,
                tg_edit_interval_max: settings.tg_edit_interval_max,
                qq_progressive_chars: settings.qq_progressive_chars,
                qq_progressive_interval: settings.qq_progressive_interval,
                feishu_update_interval: settings.feishu_update_interval,
              });
              setDirty(false);
            }}
            disabled={saving}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium bg-muted hover:bg-muted/80 transition-colors cursor-pointer disabled:opacity-50"
          >
            <RotateCw className="h-3 w-3" />
            重置
          </button>
        </div>
      )}

      <p className="text-[10px] text-muted-foreground">
        输出调优参数在下次启动渠道 Bot 时生效。调节不当可能导致消息发送异常或触发平台限流。
      </p>
    </div>
  );
}

// ── Env Fallback Guide ────────────────────────────────────

function EnvFallbackGuide() {
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        除了上方的在线配置，你也可以通过环境变量配置渠道。环境变量优先级高于在线配置。
      </p>
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-1.5">
          <Terminal className="h-3 w-3" />
          单渠道
        </p>
        <CodeBlock>{"EXCELMANUS_CHANNELS=telegram EXCELMANUS_TG_TOKEN=你的token python -m excelmanus.api"}</CodeBlock>
      </div>
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-1.5">
          <Terminal className="h-3 w-3" />
          多渠道同时启动
        </p>
        <CodeBlock>{"EXCELMANUS_CHANNELS=telegram,qq,feishu python -m excelmanus.api"}</CodeBlock>
      </div>
      <div>
        <p className="text-xs font-medium text-muted-foreground mb-1.5 flex items-center gap-1.5">
          <Terminal className="h-3 w-3" />
          飞书渠道
        </p>
        <CodeBlock>{"EXCELMANUS_CHANNELS=feishu EXCELMANUS_FEISHU_APP_ID=你的app_id EXCELMANUS_FEISHU_APP_SECRET=你的app_secret python -m excelmanus.api"}</CodeBlock>
      </div>
      <div className="mt-2 p-2.5 rounded-md bg-muted/40 border border-border/40">
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          <strong>前后端分离部署：</strong>在线配置保存在后端数据库中，前端通过 API 读写。
          无论前端部署在哪台机器，都能管理后端的渠道 Bot。
        </p>
      </div>
    </div>
  );
}

// ── Require Bind Toggle ───────────────────────────────────

function RequireBindToggle({
  checked,
  source,
  onToggle,
  saving,
}: {
  checked: boolean;
  source: "env" | "config" | "default";
  onToggle: (val: boolean) => void;
  saving: boolean;
}) {
  const isEnvLocked = source === "env";

  return (
    <div className="rounded-lg border border-border bg-gradient-to-r from-background to-muted/20 px-4 py-3 mb-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2.5 min-w-0">
          <div
            className="h-8 w-8 rounded-lg flex items-center justify-center shrink-0"
            style={{ backgroundColor: checked ? "var(--em-primary-alpha-10)" : undefined }}
          >
            <Shield
              className="h-4 w-4"
              style={{ color: checked ? "var(--em-primary)" : undefined }}
            />
          </div>
          <div className="min-w-0">
            <p className="text-xs font-medium">强制绑定前端账号</p>
            <p className="text-[10px] text-muted-foreground leading-relaxed">
              开启后，Bot 用户必须先绑定 Web 前端账号才能使用，数据跨渠道共享
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {isEnvLocked && (
            <span className="text-[9px] text-amber-600 dark:text-amber-400 font-medium whitespace-nowrap">
              环境变量锁定
            </span>
          )}
          {saving && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
          <button
            type="button"
            disabled={isEnvLocked || saving}
            onClick={() => onToggle(!checked)}
            className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed ${
              checked ? "bg-green-500" : "bg-muted-foreground/30"
            }`}
          >
            <span
              className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white transition-transform ${
                checked ? "translate-x-4.5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main Tab ──────────────────────────────────────────────

export function ChannelsTab() {
  const [status, setStatus] = useState<ChannelStatusInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<{ msg: string; type: "success" | "error" | "info" } | null>(null);
  const [savingBind, setSavingBind] = useState(false);

  const refresh = useCallback(() => {
    setError(null);
    fetchChannelsStatus()
      .then((next) => {
        setStatus(next);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const handleToast = useCallback((msg: string, type: "success" | "error" | "info") => {
    setToast({ msg, type });
  }, []);

  const activeCount = status?.channels?.length || 0;

  return (
    <div className="space-y-4">
      {/* Toast — fixed at top-center so it's visible regardless of scroll */}
      <AnimatePresence>
        {toast && (
          <div className="fixed top-4 left-1/2 -translate-x-1/2 z-100 w-[90vw] max-w-sm pointer-events-auto">
            <Toast
              message={toast.msg}
              type={toast.type}
              onClose={() => setToast(null)}
            />
          </div>
        )}
      </AnimatePresence>

      {/* Channel Configuration */}
      <Section
        title="渠道配置"
        icon={Radio}
        defaultOpen={true}
        badge={
          <div className="flex items-center gap-1.5">
            {activeCount > 0 && (
              <Badge variant="secondary" className="text-[10px] px-2 py-0.5 border-0 bg-green-500/15 text-green-600 dark:text-green-400">
                {activeCount} 运行中
              </Badge>
            )}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setLoading(true);
                refresh();
              }}
              className="h-6 w-6 inline-flex items-center justify-center rounded text-muted-foreground hover:text-foreground hover:bg-muted transition-colors cursor-pointer"
              title="刷新状态"
            >
              <RotateCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
        }
      >
        {/* 强制绑定开关 */}
        {status && (
          <RequireBindToggle
            checked={status.require_bind}
            source={status.require_bind_source}
            saving={savingBind}
            onToggle={async (val) => {
              setSavingBind(true);
              try {
                await updateChannelSettings({ require_bind: val });
                handleToast(val ? "已开启强制绑定" : "已关闭强制绑定", "success");
                refresh();
              } catch (e: unknown) {
                handleToast(
                  `设置失败: ${e instanceof Error ? e.message : String(e)}`,
                  "error",
                );
              } finally {
                setSavingBind(false);
              }
            }}
          />
        )}
        {loading && !status ? (
          <div className="flex items-center justify-center py-6 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin mr-2" />
            <span className="text-xs">加载渠道状态...</span>
          </div>
        ) : error ? (
          <div className="flex items-center gap-2 py-4 px-3 rounded-md bg-red-500/10 text-red-600 dark:text-red-400">
            <Info className="h-4 w-4 shrink-0" />
            <span className="text-xs">无法获取渠道状态: {error}</span>
          </div>
        ) : status?.details && status.details.length > 0 ? (
          <div className="space-y-2">
            <p className="text-xs text-muted-foreground mb-3">
              配置凭证后即可启动渠道 Bot。支持运行时热启停，无需重启服务。
            </p>
            {status.details.map((detail) => (
              <ChannelConfigCard
                key={detail.name}
                detail={detail}
                onRefresh={refresh}
                onToast={handleToast}
              />
            ))}
          </div>
        ) : (
          <div className="flex items-center gap-3 p-4 rounded-lg border border-dashed border-border bg-muted/20">
            <div
              className="h-10 w-10 rounded-xl flex items-center justify-center shrink-0"
              style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
            >
              <Radio className="h-5 w-5" style={{ color: "var(--em-primary)" }} />
            </div>
            <div className="min-w-0">
              <p className="text-sm font-medium">暂无可配置的渠道</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                请检查后端是否正常运行
              </p>
            </div>
          </div>
        )}
      </Section>

      {/* Access Control */}
      {status?.settings && (
        <Section title="访问控制" icon={Shield} defaultOpen={false}>
          <AccessControlSettings
            settings={status.settings}
            envOverrides={status.settings_env_overrides || {}}
            onSave={async (patch) => {
              const res = await updateChannelSettings(patch);
              if (res.locked_fields && res.locked_fields.length > 0) {
                handleToast(res.message || `部分字段被环境变量锁定`, "info");
              } else {
                handleToast("访问控制设置已保存", "success");
              }
              refresh();
            }}
          />
        </Section>
      )}

      {/* Behavior Settings */}
      {status?.settings && (
        <Section title="行为设置" icon={Settings2} defaultOpen={false}>
          <BehaviorSettings
            settings={status.settings}
            envOverrides={status.settings_env_overrides || {}}
            onSave={async (patch) => {
              const res = await updateChannelSettings(patch);
              if (res.locked_fields && res.locked_fields.length > 0) {
                handleToast(res.message || `部分字段被环境变量锁定`, "info");
              } else {
                handleToast("行为设置已保存", "success");
              }
              refresh();
            }}
          />
        </Section>
      )}

      {/* Output Tuning */}
      {status?.settings && (
        <Section title="输出调优" icon={Zap} defaultOpen={false}>
          <OutputTuningSettings
            settings={status.settings}
            onSave={async (patch) => {
              await updateChannelSettings(patch);
              handleToast("输出调优设置已保存", "success");
              refresh();
            }}
          />
        </Section>
      )}

      {/* Env Var Fallback */}
      <Section title="环境变量配置（高级）" icon={Terminal} defaultOpen={false}>
        <EnvFallbackGuide />
      </Section>

      {/* Bot Commands Reference */}
      <Section
        title="Bot 命令参考"
        icon={BookOpen}
        defaultOpen={false}
        badge={
          <Badge variant="secondary" className="text-[10px] px-2 py-0.5 border-0 bg-muted text-muted-foreground">
            22 个命令
          </Badge>
        }
      >
        <div className="space-y-1">
          <p className="text-xs text-muted-foreground mb-3">
            在 Bot 对话中使用以下命令控制 ExcelManus。所有命令均以 <code className="px-1 py-0.5 rounded bg-muted font-mono">/</code> 开头。
          </p>
          <CommandReference />
          <ConcurrencyModesInfo />
          {status?.rate_limit && (
            <RateLimitSettings
              config={status.rate_limit}
              envOverrides={status.rate_limit_env_overrides || {}}
              onToast={handleToast}
              onRefresh={refresh}
            />
          )}
          <BindFlowOverview />
        </div>
      </Section>
    </div>
  );
}
