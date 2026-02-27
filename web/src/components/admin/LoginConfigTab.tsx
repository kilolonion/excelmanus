"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Github, Mail, MailCheck, Loader2, AlertCircle, CheckCircle, Save, Eye, EyeOff } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import {
  fetchLoginConfig,
  updateLoginConfig,
  type LoginConfig,
} from "@/lib/auth-api";

/* ── Toggle switch ───────────────────────────────── */

interface ToggleProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  checked: boolean;
  loading: boolean;
  onChange: (val: boolean) => void;
}

function Toggle({ icon, title, description, checked, loading, onChange }: ToggleProps) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3 min-w-0">
        <div
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
          style={{ backgroundColor: "var(--em-primary)", opacity: 0.12 }}
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
        className={`relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-offset-2 disabled:opacity-50 disabled:cursor-not-allowed ${
          checked ? "bg-[var(--em-primary)]" : "bg-muted-foreground/30"
        }`}
        style={checked ? { backgroundColor: "var(--em-primary)" } : undefined}
      >
        <span
          className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
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

/* ── Secret input with toggle visibility ─────────── */

function SecretInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const [visible, setVisible] = useState(false);
  const isAllStars = value.length > 0 && /^\*+[^\s]{0,4}$/.test(value);
  return (
    <div>
      <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
      <div className="relative">
        <input
          type={visible ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => {
            if (isAllStars) onChange("");
          }}
          placeholder={placeholder}
          className="w-full h-8 rounded-md border border-border bg-background px-2.5 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/40 font-mono"
        />
        <button
          type="button"
          onClick={() => setVisible(!visible)}
          className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
          tabIndex={-1}
        >
          {visible ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
        </button>
      </div>
    </div>
  );
}

/* ── Plain text input ────────────────────────────── */

function TextInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs font-medium text-muted-foreground mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full h-8 rounded-md border border-border bg-background px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/40 font-mono"
      />
    </div>
  );
}

/* ── Credential section with save button ─────────── */

interface CredentialSectionProps {
  icon: React.ReactNode;
  title: string;
  description: string;
  children: React.ReactNode;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
}

function CredentialSection({ icon, title, description, children, dirty, saving, onSave }: CredentialSectionProps) {
  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <div className="flex items-start gap-3 p-4 pb-2">
        <div
          className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg"
          style={{ backgroundColor: "var(--em-primary)", opacity: 0.12 }}
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
            className="h-7 text-xs gap-1.5 text-white"
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

/* ── Toast ────────────────────────────────────────── */

function Toast({ message, type, onClose }: { message: string; type: "success" | "error"; onClose: () => void }) {
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
      <button onClick={onClose} className="opacity-60 hover:opacity-100">×</button>
    </motion.div>
  );
}

/* ── Main component ──────────────────────────────── */

export default function LoginConfigTab() {
  const [config, setConfig] = useState<LoginConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [toggling, setToggling] = useState<string | null>(null);
  const [toast, setToast] = useState<{ message: string; type: "success" | "error" } | null>(null);
  const toastTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

  // Draft state for credential forms
  const [ghDraft, setGhDraft] = useState({ client_id: "", client_secret: "", redirect_uri: "" });
  const [goDraft, setGoDraft] = useState({ client_id: "", client_secret: "", redirect_uri: "" });
  const [emDraft, setEmDraft] = useState({
    resend_api_key: "", smtp_host: "", smtp_port: "", smtp_user: "", smtp_password: "", from: "",
  });
  const [ghDirty, setGhDirty] = useState(false);
  const [goDirty, setGoDirty] = useState(false);
  const [emDirty, setEmDirty] = useState(false);
  const [saving, setSaving] = useState<string | null>(null);

  const showToast = useCallback((message: string, type: "success" | "error") => {
    setToast({ message, type });
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(null), 3000);
  }, []);

  const syncDrafts = useCallback((c: LoginConfig) => {
    setGhDraft({ client_id: c.github_client_id, client_secret: c.github_client_secret, redirect_uri: c.github_redirect_uri });
    setGoDraft({ client_id: c.google_client_id, client_secret: c.google_client_secret, redirect_uri: c.google_redirect_uri });
    setEmDraft({
      resend_api_key: c.email_resend_api_key, smtp_host: c.email_smtp_host,
      smtp_port: c.email_smtp_port, smtp_user: c.email_smtp_user,
      smtp_password: c.email_smtp_password, from: c.email_from,
    });
    setGhDirty(false);
    setGoDirty(false);
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
  }, [syncDrafts, showToast]);

  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const handleToggle = useCallback(
    async (key: keyof LoginConfig, value: boolean) => {
      if (!config) return;
      setToggling(key);
      try {
        const updated = await updateLoginConfig({ [key]: value });
        setConfig(updated);
        syncDrafts(updated);
        showToast("已更新", "success");
      } catch (err) {
        showToast(err instanceof Error ? err.message : "更新失败", "error");
      } finally {
        setToggling(null);
      }
    },
    [config, syncDrafts, showToast],
  );

  const handleSaveCredentials = useCallback(
    async (section: "github" | "google" | "email") => {
      setSaving(section);
      try {
        let payload: Partial<LoginConfig> = {};
        if (section === "github") {
          payload = {
            github_client_id: ghDraft.client_id,
            github_client_secret: ghDraft.client_secret,
            github_redirect_uri: ghDraft.redirect_uri,
          };
        } else if (section === "google") {
          payload = {
            google_client_id: goDraft.client_id,
            google_client_secret: goDraft.client_secret,
            google_redirect_uri: goDraft.redirect_uri,
          };
        } else {
          payload = {
            email_resend_api_key: emDraft.resend_api_key,
            email_smtp_host: emDraft.smtp_host,
            email_smtp_port: emDraft.smtp_port,
            email_smtp_user: emDraft.smtp_user,
            email_smtp_password: emDraft.smtp_password,
            email_from: emDraft.from,
          };
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
    },
    [ghDraft, goDraft, emDraft, syncDrafts, showToast],
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!config) {
    return (
      <div className="rounded-lg bg-destructive/10 border border-destructive/20 px-4 py-3 text-sm text-destructive flex items-center gap-2">
        <AlertCircle className="h-4 w-4 flex-shrink-0" />
        加载登录配置失败
      </div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="space-y-6"
    >
      {/* Toast */}
      <AnimatePresence>
        {toast && <Toast message={toast.message} type={toast.type} onClose={() => setToast(null)} />}
      </AnimatePresence>

      {/* ── GitHub ──────────────────────────────── */}
      <div>
        <h3 className="text-sm font-semibold mb-1">GitHub 登录</h3>
        <p className="text-xs text-muted-foreground mb-3">
          配置 GitHub OAuth 应用凭据。需要在 GitHub 开发者设置中创建 OAuth App。
        </p>
        <div className="space-y-2">
          <Toggle
            icon={<Github className="h-4 w-4" />}
            title="启用 GitHub 登录"
            description="允许用户使用 GitHub 账号登录或注册"
            checked={config.login_github_enabled}
            loading={toggling === "login_github_enabled"}
            onChange={(val) => handleToggle("login_github_enabled", val)}
          />
          <CredentialSection
            icon={<Github className="h-4 w-4" />}
            title="GitHub OAuth 凭据"
            description="从 GitHub Developer Settings → OAuth Apps 获取"
            dirty={ghDirty}
            saving={saving === "github"}
            onSave={() => handleSaveCredentials("github")}
          >
            <TextInput
              label="Client ID"
              value={ghDraft.client_id}
              onChange={(v) => { setGhDraft((d) => ({ ...d, client_id: v })); setGhDirty(true); }}
              placeholder="如 Iv1.xxxxxxxxxx"
            />
            <SecretInput
              label="Client Secret"
              value={ghDraft.client_secret}
              onChange={(v) => { setGhDraft((d) => ({ ...d, client_secret: v })); setGhDirty(true); }}
              placeholder="如 xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
            />
            <TextInput
              label="Redirect URI"
              value={ghDraft.redirect_uri}
              onChange={(v) => { setGhDraft((d) => ({ ...d, redirect_uri: v })); setGhDirty(true); }}
              placeholder="如 http://localhost:3000/api/v1/auth/oauth/github/callback"
            />
          </CredentialSection>
        </div>
      </div>

      {/* ── Google ─────────────────────────────── */}
      <div className="border-t border-border pt-6">
        <h3 className="text-sm font-semibold mb-1">Google 登录</h3>
        <p className="text-xs text-muted-foreground mb-3">
          配置 Google OAuth 凭据。需要在 Google Cloud Console 中创建 OAuth 2.0 客户端。
        </p>
        <div className="space-y-2">
          <Toggle
            icon={<Mail className="h-4 w-4" />}
            title="启用 Google 登录"
            description="允许用户使用 Google 账号登录或注册"
            checked={config.login_google_enabled}
            loading={toggling === "login_google_enabled"}
            onChange={(val) => handleToggle("login_google_enabled", val)}
          />
          <CredentialSection
            icon={<Mail className="h-4 w-4" />}
            title="Google OAuth 凭据"
            description="从 Google Cloud Console → APIs & Services → Credentials 获取"
            dirty={goDirty}
            saving={saving === "google"}
            onSave={() => handleSaveCredentials("google")}
          >
            <TextInput
              label="Client ID"
              value={goDraft.client_id}
              onChange={(v) => { setGoDraft((d) => ({ ...d, client_id: v })); setGoDirty(true); }}
              placeholder="如 xxxxxxxxxxxx.apps.googleusercontent.com"
            />
            <SecretInput
              label="Client Secret"
              value={goDraft.client_secret}
              onChange={(v) => { setGoDraft((d) => ({ ...d, client_secret: v })); setGoDirty(true); }}
              placeholder="如 GOCSPX-xxxxxxxxxxxxxxxxx"
            />
            <TextInput
              label="Redirect URI"
              value={goDraft.redirect_uri}
              onChange={(v) => { setGoDraft((d) => ({ ...d, redirect_uri: v })); setGoDirty(true); }}
              placeholder="如 http://localhost:3000/api/v1/auth/oauth/google/callback"
            />
          </CredentialSection>
        </div>
      </div>

      {/* ── 邮件 ───────────────────────────────── */}
      <div className="border-t border-border pt-6">
        <h3 className="text-sm font-semibold mb-1">邮箱验证</h3>
        <p className="text-xs text-muted-foreground mb-3">
          配置邮件发送服务。支持 Resend API（推荐）或 SMTP。开启邮箱验证需要至少配置其中一种。
        </p>
        <div className="space-y-2">
          <Toggle
            icon={<MailCheck className="h-4 w-4" />}
            title="启用邮箱验证"
            description="注册时要求邮箱验证码验证"
            checked={config.email_verify_required}
            loading={toggling === "email_verify_required"}
            onChange={(val) => handleToggle("email_verify_required", val)}
          />
          <CredentialSection
            icon={<MailCheck className="h-4 w-4" />}
            title="邮件服务配置"
            description="Resend API Key 优先；未配置时回退到 SMTP"
            dirty={emDirty}
            saving={saving === "email"}
            onSave={() => handleSaveCredentials("email")}
          >
            <div className="rounded-md bg-muted/50 px-3 py-2 mb-1">
              <p className="text-[11px] font-medium text-muted-foreground mb-1.5">Resend（推荐）</p>
              <SecretInput
                label="API Key"
                value={emDraft.resend_api_key}
                onChange={(v) => { setEmDraft((d) => ({ ...d, resend_api_key: v })); setEmDirty(true); }}
                placeholder="如 re_xxxxxxxxxx"
              />
            </div>
            <div className="rounded-md bg-muted/50 px-3 py-2">
              <p className="text-[11px] font-medium text-muted-foreground mb-1.5">SMTP（备用）</p>
              <div className="space-y-2">
                <div className="grid grid-cols-3 gap-2">
                  <div className="col-span-2">
                    <TextInput
                      label="SMTP 主机"
                      value={emDraft.smtp_host}
                      onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_host: v })); setEmDirty(true); }}
                      placeholder="如 smtp.gmail.com"
                    />
                  </div>
                  <TextInput
                    label="端口"
                    value={emDraft.smtp_port}
                    onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_port: v })); setEmDirty(true); }}
                    placeholder="465"
                  />
                </div>
                <TextInput
                  label="用户名"
                  value={emDraft.smtp_user}
                  onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_user: v })); setEmDirty(true); }}
                  placeholder="如 you@gmail.com"
                />
                <SecretInput
                  label="密码"
                  value={emDraft.smtp_password}
                  onChange={(v) => { setEmDraft((d) => ({ ...d, smtp_password: v })); setEmDirty(true); }}
                  placeholder="应用专用密码"
                />
              </div>
            </div>
            <TextInput
              label="发件人地址"
              value={emDraft.from}
              onChange={(v) => { setEmDraft((d) => ({ ...d, from: v })); setEmDirty(true); }}
              placeholder="如 ExcelManus <noreply@example.com>"
            />
          </CredentialSection>
        </div>
      </div>
    </motion.div>
  );
}
