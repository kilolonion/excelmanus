"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle,
  Eye,
  EyeOff,
  Github,
  Loader2,
  Mail,
  MailCheck,
  Save,
  ScrollText,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

import { Button } from "@/components/ui/button";
import {
  fetchLoginConfig,
  updateLoginConfig,
  type LoginConfig,
} from "@/lib/auth-api";

type LoginToggleKey =
  | "login_github_enabled"
  | "login_google_enabled"
  | "login_qq_enabled"
  | "email_verify_required"
  | "require_agreement";

type SaveSection = "github" | "google" | "gemini" | "qq" | "email";

function withToggleValue(
  config: LoginConfig | null,
  key: LoginToggleKey,
  value: boolean,
): LoginConfig | null {
  if (!config) return config;
  return { ...config, [key]: value };
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  secret = false,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  secret?: boolean;
}) {
  const [visible, setVisible] = useState(false);
  const isMasked = value.length > 0 && /^\*+[^\s]{0,4}$/.test(value);

  return (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-muted-foreground">{label}</label>
      <div className="relative">
        <input
          type={secret && !visible ? "password" : "text"}
          value={value}
          onFocus={() => {
            if (isMasked) onChange("");
          }}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full h-8 rounded-md border border-border bg-background px-2.5 pr-8 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/40"
        />
        {secret && (
          <button
            type="button"
            className="absolute right-1.5 top-1/2 -translate-y-1/2 h-6 w-6 flex items-center justify-center text-muted-foreground hover:text-foreground"
            onClick={() => setVisible((v) => !v)}
            tabIndex={-1}
          >
            {visible ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
          </button>
        )}
      </div>
    </div>
  );
}

function ToggleCard({
  icon,
  title,
  description,
  checked,
  loading,
  onChange,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  checked: boolean;
  loading: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3 min-w-0">
        <div
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
          style={{ backgroundColor: "color-mix(in srgb, var(--em-primary) 12%, transparent)" }}
        >
          <span style={{ color: "var(--em-primary)" }}>{icon}</span>
        </div>
        <div className="min-w-0">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        </div>
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={loading}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 min-w-[2.75rem] rounded-full border-2 border-transparent transition-colors disabled:opacity-50 ${
          checked ? "bg-[var(--em-primary)]" : "bg-muted-foreground/30"
        }`}
      >
        <span
          className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${
            checked ? "translate-x-5" : "translate-x-0"
          }`}
        />
        {loading && (
          <span className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-3 w-3 animate-spin text-white" />
          </span>
        )}
      </button>
    </div>
  );
}

function CredentialSection({
  icon,
  title,
  description,
  dirty,
  saving,
  onSave,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex items-start gap-3 p-4 pb-2">
        <div
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
          style={{ backgroundColor: "color-mix(in srgb, var(--em-primary) 12%, transparent)" }}
        >
          <span style={{ color: "var(--em-primary)" }}>{icon}</span>
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{title}</p>
          <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
        </div>
      </div>
      <div className="px-4 pb-4 space-y-2.5">
        {children}
        <div className="flex justify-end pt-1">
          <Button
            size="sm"
            className="h-7 text-xs gap-1.5 px-2.5 text-white"
            style={{ backgroundColor: "var(--em-primary)" }}
            disabled={!dirty || saving}
            onClick={onSave}
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />}
            保存
          </Button>
        </div>
      </div>
    </div>
  );
}

function Toast({
  message,
  type,
  onClose,
}: {
  message: string;
  type: "success" | "error";
  onClose: () => void;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -8 }}
      className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs ${
        type === "success"
          ? "bg-green-50 dark:bg-green-950/50 border-green-200 dark:border-green-800 text-green-800 dark:text-green-200"
          : "bg-red-50 dark:bg-red-950/50 border-red-200 dark:border-red-800 text-red-800 dark:text-red-200"
      }`}
    >
      {type === "success" ? <CheckCircle className="h-3.5 w-3.5" /> : <AlertCircle className="h-3.5 w-3.5" />}
      <span className="flex-1">{message}</span>
      <button onClick={onClose} className="h-5 w-5 flex items-center justify-center opacity-60 hover:opacity-100">
        ×
      </button>
    </motion.div>
  );
}

export default function LoginConfigTab() {
  const [config, setConfig] = useState<LoginConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);
  const [saving, setSaving] = useState<SaveSection | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [ghDraft, setGhDraft] = useState({ client_id: "", client_secret: "", redirect_uri: "" });
  const [goDraft, setGoDraft] = useState({ client_id: "", client_secret: "", redirect_uri: "" });
  const [geminiDraft, setGeminiDraft] = useState({ client_id: "", client_secret: "" });
  const [qqDraft, setQqDraft] = useState({ client_id: "", client_secret: "", redirect_uri: "" });
  const [emDraft, setEmDraft] = useState({
    resend_api_key: "",
    smtp_host: "",
    smtp_port: "",
    smtp_user: "",
    smtp_password: "",
    from: "",
  });

  const [ghDirty, setGhDirty] = useState(false);
  const [goDirty, setGoDirty] = useState(false);
  const [geminiDirty, setGeminiDirty] = useState(false);
  const [qqDirty, setQqDirty] = useState(false);
  const [emDirty, setEmDirty] = useState(false);

  const showToast = useCallback((message: string, type: "success" | "error") => {
    setToast({ message, type });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  const syncDrafts = useCallback((c: LoginConfig) => {
    setGhDraft({ client_id: c.github_client_id, client_secret: c.github_client_secret, redirect_uri: c.github_redirect_uri });
    setGoDraft({ client_id: c.google_client_id, client_secret: c.google_client_secret, redirect_uri: c.google_redirect_uri });
    setGeminiDraft({ client_id: c.gemini_oauth_client_id, client_secret: c.gemini_oauth_client_secret });
    setQqDraft({ client_id: c.qq_client_id, client_secret: c.qq_client_secret, redirect_uri: c.qq_redirect_uri });
    setEmDraft({
      resend_api_key: c.email_resend_api_key,
      smtp_host: c.email_smtp_host,
      smtp_port: c.email_smtp_port,
      smtp_user: c.email_smtp_user,
      smtp_password: c.email_smtp_password,
      from: c.email_from,
    });
    setGhDirty(false);
    setGoDirty(false);
    setGeminiDirty(false);
    setQqDirty(false);
    setEmDirty(false);
  }, []);

  const loadConfig = useCallback(async () => {
    try {
      setLoading(true);
      const data = await fetchLoginConfig();
      setConfig(data);
      syncDrafts(data);
    } catch (err) {
      showToast(err instanceof Error ? err.message : "加载登录配置失败", "error");
    } finally {
      setLoading(false);
    }
  }, [showToast, syncDrafts]);

  useEffect(() => {
    void loadConfig();
    return () => {
      if (toastTimer.current) clearTimeout(toastTimer.current);
    };
  }, [loadConfig]);

  const handleToggle = useCallback(async (key: LoginToggleKey, value: boolean) => {
    if (!config) return;
    const previous = config[key];
    setToggling(key);
    setConfig((prev) => withToggleValue(prev, key, value));
    try {
      const updated = await updateLoginConfig({ [key]: value });
      setConfig(updated);
      syncDrafts(updated);
      showToast("已更新", "success");
    } catch (err) {
      setConfig((prev) => withToggleValue(prev, key, previous));
      showToast(err instanceof Error ? err.message : "更新失败", "error");
    } finally {
      setToggling(null);
    }
  }, [config, showToast, syncDrafts]);

  const handleSaveCredentials = useCallback(async (section: SaveSection) => {
    setSaving(section);
    try {
      let payload: Partial<LoginConfig>;
      switch (section) {
        case "github":
          payload = {
            github_client_id: ghDraft.client_id,
            github_client_secret: ghDraft.client_secret,
            github_redirect_uri: ghDraft.redirect_uri,
          };
          break;
        case "google":
          payload = {
            google_client_id: goDraft.client_id,
            google_client_secret: goDraft.client_secret,
            google_redirect_uri: goDraft.redirect_uri,
          };
          break;
        case "gemini":
          payload = {
            gemini_oauth_client_id: geminiDraft.client_id,
            gemini_oauth_client_secret: geminiDraft.client_secret,
          };
          break;
        case "qq":
          payload = {
            qq_client_id: qqDraft.client_id,
            qq_client_secret: qqDraft.client_secret,
            qq_redirect_uri: qqDraft.redirect_uri,
          };
          break;
        case "email":
          payload = {
            email_resend_api_key: emDraft.resend_api_key,
            email_smtp_host: emDraft.smtp_host,
            email_smtp_port: emDraft.smtp_port,
            email_smtp_user: emDraft.smtp_user,
            email_smtp_password: emDraft.smtp_password,
            email_from: emDraft.from,
          };
          break;
      }
      const updated = await updateLoginConfig(payload);
      setConfig(updated);
      syncDrafts(updated);
      showToast("凭据已保存", "success");
    } catch (err) {
      showToast(err instanceof Error ? err.message : "保存失败", "error");
    } finally {
      setSaving(null);
    }
  }, [emDraft, geminiDraft, ghDraft, goDraft, qqDraft, showToast, syncDrafts]);

  const sections = useMemo(() => {
    if (!config) return null;
    return {
      github: (
        <div>
          <h3 className="text-sm font-semibold mb-1">GitHub 登录</h3>
          <p className="text-xs text-muted-foreground mb-3">配置 GitHub 登录开关和 OAuth 凭据。</p>
          <div className="space-y-2">
            <ToggleCard
              icon={<Github className="h-4 w-4" />}
              title="启用 GitHub 登录"
              description="允许用户使用 GitHub 账号登录或注册"
              checked={config.login_github_enabled}
              loading={toggling === "login_github_enabled"}
              onChange={(value) => void handleToggle("login_github_enabled", value)}
            />
            <CredentialSection
              icon={<Github className="h-4 w-4" />}
              title="GitHub OAuth 凭据"
              description="从 GitHub Developer Settings -> OAuth Apps 获取"
              dirty={ghDirty}
              saving={saving === "github"}
              onSave={() => void handleSaveCredentials("github")}
            >
              <Field label="Client ID" value={ghDraft.client_id} onChange={(v) => { setGhDraft((d) => ({ ...d, client_id: v })); setGhDirty(true); }} placeholder="例如 Iv1.xxxxxxxxxx" />
              <Field label="Client Secret" value={ghDraft.client_secret} onChange={(v) => { setGhDraft((d) => ({ ...d, client_secret: v })); setGhDirty(true); }} placeholder="例如 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" secret />
              <Field label="Redirect URI" value={ghDraft.redirect_uri} onChange={(v) => { setGhDraft((d) => ({ ...d, redirect_uri: v })); setGhDirty(true); }} placeholder="例如 http://localhost:3000/api/v1/auth/oauth/github/callback" />
            </CredentialSection>
          </div>
        </div>
      ),
      google: (
        <div className="border-t border-border pt-6">
          <h3 className="text-sm font-semibold mb-1">Google 登录</h3>
          <p className="text-xs text-muted-foreground mb-3">配置 Google 登录开关和 OAuth 凭据。</p>
          <div className="space-y-2">
            <ToggleCard
              icon={<Mail className="h-4 w-4" />}
              title="启用 Google 登录"
              description="允许用户使用 Google 账号登录或注册"
              checked={config.login_google_enabled}
              loading={toggling === "login_google_enabled"}
              onChange={(value) => void handleToggle("login_google_enabled", value)}
            />
            <CredentialSection
              icon={<Mail className="h-4 w-4" />}
              title="Google OAuth 凭据"
              description="从 Google Cloud Console -> APIs & Services -> Credentials 获取"
              dirty={goDirty}
              saving={saving === "google"}
              onSave={() => void handleSaveCredentials("google")}
            >
              <Field label="Client ID" value={goDraft.client_id} onChange={(v) => { setGoDraft((d) => ({ ...d, client_id: v })); setGoDirty(true); }} placeholder="例如 xxxxxxxxxxxx.apps.googleusercontent.com" />
              <Field label="Client Secret" value={goDraft.client_secret} onChange={(v) => { setGoDraft((d) => ({ ...d, client_secret: v })); setGoDirty(true); }} placeholder="例如 GOCSPX-xxxxxxxxxxxxxxxxx" secret />
              <Field label="Redirect URI" value={goDraft.redirect_uri} onChange={(v) => { setGoDraft((d) => ({ ...d, redirect_uri: v })); setGoDirty(true); }} placeholder="例如 http://localhost:3000/api/v1/auth/oauth/google/callback" />
            </CredentialSection>
          </div>
        </div>
      ),
      gemini: (
        <div className="border-t border-border pt-6">
          <h3 className="text-sm font-semibold mb-1">Gemini 订阅 OAuth</h3>
          <p className="text-xs text-muted-foreground mb-3">
            可选覆盖 Gemini 订阅登录使用的 OAuth 客户端凭据。留空时回退到内置 Desktop App 凭据。
          </p>
          <div className="space-y-2">
            <CredentialSection
              icon={<MailCheck className="h-4 w-4" />}
              title="Gemini OAuth 客户端"
              description="建议在 Google Cloud Console 中单独创建这组凭据"
              dirty={geminiDirty}
              saving={saving === "gemini"}
              onSave={() => void handleSaveCredentials("gemini")}
            >
              <Field label="Client ID" value={geminiDraft.client_id} onChange={(v) => { setGeminiDraft((d) => ({ ...d, client_id: v })); setGeminiDirty(true); }} placeholder="例如 1234567890-xxxx.apps.googleusercontent.com" />
              <Field label="Client Secret" value={geminiDraft.client_secret} onChange={(v) => { setGeminiDraft((d) => ({ ...d, client_secret: v })); setGeminiDirty(true); }} placeholder="例如 GOCSPX-xxxxxxxxxxxxxxxx" secret />
            </CredentialSection>
          </div>
        </div>
      ),
      qq: (
        <div className="border-t border-border pt-6">
          <h3 className="text-sm font-semibold mb-1">QQ 登录</h3>
          <p className="text-xs text-muted-foreground mb-3">配置 QQ 登录开关和 OAuth 凭据。</p>
          <div className="space-y-2">
            <ToggleCard
              icon={<Mail className="h-4 w-4" />}
              title="启用 QQ 登录"
              description="允许用户使用 QQ 账号登录或注册"
              checked={config.login_qq_enabled}
              loading={toggling === "login_qq_enabled"}
              onChange={(value) => void handleToggle("login_qq_enabled", value)}
            />
            <CredentialSection
              icon={<Mail className="h-4 w-4" />}
              title="QQ OAuth 凭据"
              description="从 QQ 互联平台 -> 应用管理 获取"
              dirty={qqDirty}
              saving={saving === "qq"}
              onSave={() => void handleSaveCredentials("qq")}
            >
              <Field label="APP ID" value={qqDraft.client_id} onChange={(v) => { setQqDraft((d) => ({ ...d, client_id: v })); setQqDirty(true); }} placeholder="例如 101xxxxxx" />
              <Field label="APP Key" value={qqDraft.client_secret} onChange={(v) => { setQqDraft((d) => ({ ...d, client_secret: v })); setQqDirty(true); }} placeholder="例如 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" secret />
              <Field label="Redirect URI" value={qqDraft.redirect_uri} onChange={(v) => { setQqDraft((d) => ({ ...d, redirect_uri: v })); setQqDirty(true); }} placeholder="例如 http://localhost:3000/api/v1/auth/oauth/qq/callback" />
            </CredentialSection>
          </div>
        </div>
      ),
      email: (
        <div className="border-t border-border pt-6">
          <h3 className="text-sm font-semibold mb-1">邮件与协议</h3>
          <p className="text-xs text-muted-foreground mb-3">配置邮箱验证、发信服务和用户协议开关。</p>
          <div className="space-y-2">
            <ToggleCard
              icon={<MailCheck className="h-4 w-4" />}
              title="启用邮箱验证"
              description="注册时要求邮箱验证码验证"
              checked={config.email_verify_required}
              loading={toggling === "email_verify_required"}
              onChange={(value) => void handleToggle("email_verify_required", value)}
            />
            <ToggleCard
              icon={<ScrollText className="h-4 w-4" />}
              title="要求同意用户协议"
              description="登录和注册时必须勾选同意服务条款和隐私政策"
              checked={config.require_agreement}
              loading={toggling === "require_agreement"}
              onChange={(value) => void handleToggle("require_agreement", value)}
            />
            <CredentialSection
              icon={<MailCheck className="h-4 w-4" />}
              title="邮件服务配置"
              description="优先使用 Resend；未配置时回退到 SMTP"
              dirty={emDirty}
              saving={saving === "email"}
              onSave={() => void handleSaveCredentials("email")}
            >
              <Field label="Resend API Key" value={emDraft.resend_api_key} onChange={(v) => { setEmDraft((d) => ({ ...d, resend_api_key: v })); setEmDirty(true); }} placeholder="例如 re_xxxxxxxxxx" secret />
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
                <Field label="SMTP Host" value={emDraft.smtp_host} onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_host: v })); setEmDirty(true); }} placeholder="例如 smtp.gmail.com" />
                <Field label="SMTP Port" value={emDraft.smtp_port} onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_port: v })); setEmDirty(true); }} placeholder="465" />
                <Field label="SMTP User" value={emDraft.smtp_user} onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_user: v })); setEmDirty(true); }} placeholder="例如 you@gmail.com" />
                <Field label="SMTP Password" value={emDraft.smtp_password} onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_password: v })); setEmDirty(true); }} placeholder="应用专用密码" secret />
              </div>
              <Field label="Email From" value={emDraft.from} onChange={(v) => { setEmDraft((d) => ({ ...d, from: v })); setEmDirty(true); }} placeholder="例如 ExcelManus <noreply@example.com>" />
            </CredentialSection>
          </div>
        </div>
      ),
    };
  }, [
    config,
    toggling,
    saving,
    ghDirty,
    goDirty,
    geminiDirty,
    qqDirty,
    emDirty,
    ghDraft,
    goDraft,
    geminiDraft,
    qqDraft,
    emDraft,
    handleToggle,
    handleSaveCredentials,
  ]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!config || !sections) {
    return (
      <div className="rounded-lg bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive flex items-center gap-2">
        <AlertCircle className="h-4 w-4 flex-shrink-0" />
        加载登录配置失败
      </div>
    );
  }

  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
      <AnimatePresence>
        {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
      </AnimatePresence>

      {sections.github}
      {sections.google}
      {sections.gemini}
      {sections.qq}
      {sections.email}
    </motion.div>
  );
}
