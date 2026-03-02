"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Camera,
  Check,
  Eye,
  EyeOff,
  Loader2,
  Mail,
  KeyRound,
  User,
  AlertCircle,
  Shield,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/stores/auth-store";
import { resolveAvatarSrc } from "@/lib/api";
import {
  updateProfile,
  changePassword,
  changeEmail,
  uploadAvatar,
  fetchMyWorkspaceUsage,
  fetchOAuthLinks,
  unlinkOAuth,
  getOAuthUrl,
  fetchCodexStatus,
  codexOAuthStart,
  codexOAuthExchange,
  codexDeviceCodeStart,
  codexDeviceCodePoll,
  connectCodex,
  disconnectCodex,
  refreshCodexToken,
  type WorkspaceUsage,
  type OAuthLinkInfo,
  type CodexStatus,
} from "@/lib/auth-api";
import { useAuthConfigStore } from "@/stores/auth-config-store";

const cardVariants = {
  hidden: { opacity: 0, y: 16 },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    transition: { delay: i * 0.06, duration: 0.35, ease: "easeOut" as const },
  }),
};

// ── Toast ──────────────────────────────────────────────────

function Toast({
  message,
  type,
  onClose,
}: {
  message: string;
  type: "success" | "error";
  onClose: () => void;
}) {
  useEffect(() => {
    const t = setTimeout(onClose, 3000);
    return () => clearTimeout(t);
  }, [onClose]);

  return (
    <motion.div
      initial={{ opacity: 0, y: -20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      className={`fixed top-4 right-4 z-50 flex items-center gap-2 px-4 py-3 rounded-lg shadow-lg text-sm font-medium ${
        type === "success"
          ? "bg-green-600 text-white"
          : "bg-red-600 text-white"
      }`}
    >
      {type === "success" ? (
        <Check className="h-4 w-4" />
      ) : (
        <AlertCircle className="h-4 w-4" />
      )}
      {message}
    </motion.div>
  );
}

// ── Avatar ─────────────────────────────────────────────────

function ProfileAvatar({
  src,
  name,
  onUpload,
  uploading,
}: {
  src?: string | null;
  name: string;
  onUpload: (file: File) => void;
  uploading: boolean;
}) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const accessToken = useAuthStore((s) => s.accessToken);

  const proxied = resolveAvatarSrc(src, accessToken);

  const initial = name[0]?.toUpperCase() || "U";
  const showImage = proxied && failedSrc !== src;

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onUpload(file);
    e.target.value = "";
  };

  return (
    <div className="relative group">
      <div className="relative h-24 w-24 rounded-full overflow-hidden ring-4 ring-background shadow-lg">
        {showImage ? (
          <img
            src={proxied}
            alt=""
            className="h-full w-full object-cover"
            referrerPolicy="no-referrer"
            onError={() => setFailedSrc(src ?? null)}
          />
        ) : (
          <span
            className="h-full w-full flex items-center justify-center text-3xl font-bold text-white"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            {initial}
          </span>
        )}
        {uploading && (
          <div className="absolute inset-0 bg-black/50 flex items-center justify-center">
            <Loader2 className="h-6 w-6 text-white animate-spin" />
          </div>
        )}
      </div>
      <button
        onClick={() => inputRef.current?.click()}
        disabled={uploading}
        className="absolute bottom-0 right-0 h-8 w-8 rounded-full flex items-center justify-center text-white shadow-md transition-transform hover:scale-110 cursor-pointer"
        style={{ backgroundColor: "var(--em-primary)" }}
        title="更换头像"
      >
        <Camera className="h-4 w-4" />
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        className="hidden"
        onChange={handleChange}
      />
    </div>
  );
}

// ── Provider helpers ──────────────────────────────────────

const PROVIDER_META: Record<string, { label: string; color: string }> = {
  github: { label: "GitHub", color: "#24292f" },
  google: { label: "Google", color: "#4285f4" },
  qq: { label: "QQ", color: "#12b7f5" },
};

function ProviderIcon({ provider, className }: { provider: string; className?: string }) {
  if (provider === "github")
    return (
      <svg className={className} viewBox="0 0 24 24" fill="currentColor">
        <path d="M12 .5C5.37.5 0 5.78 0 12.292c0 5.211 3.438 9.63 8.205 11.188.6.111.82-.254.82-.567 0-.28-.01-1.022-.015-2.005-3.338.711-4.042-1.582-4.042-1.582-.546-1.361-1.335-1.724-1.335-1.724-1.087-.731.084-.716.084-.716 1.205.082 1.838 1.215 1.838 1.215 1.07 1.803 2.809 1.282 3.495.981.108-.763.417-1.282.76-1.577-2.665-.295-5.466-1.309-5.466-5.827 0-1.287.465-2.339 1.235-3.164-.135-.298-.54-1.497.105-3.121 0 0 1.005-.316 3.3 1.209A11.707 11.707 0 0 1 12 6.545c1.02.004 2.047.135 3.004.397 2.28-1.525 3.285-1.209 3.285-1.209.645 1.624.24 2.823.12 3.121.765.825 1.23 1.877 1.23 3.164 0 4.53-2.805 5.527-5.475 5.817.42.354.81 1.077.81 2.182 0 1.578-.015 2.846-.015 3.229 0 .309.21.678.825.56C20.565 21.917 24 17.495 24 12.292 24 5.78 18.627.5 12 .5z" />
      </svg>
    );
  if (provider === "google")
    return (
      <svg className={className} viewBox="0 0 24 24" fill="currentColor">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
      </svg>
    );
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor">
      <circle cx="12" cy="12" r="10" />
    </svg>
  );
}

// ── OAuth Links Section ───────────────────────────────────

function OAuthLinksSection({
  showToast,
}: {
  showToast: (msg: string, type: "success" | "error") => void;
}) {
  const [links, setLinks] = useState<OAuthLinkInfo[]>([]);
  const [hasPassword, setHasPassword] = useState(true);
  const [loading, setLoading] = useState(true);
  const [unlinking, setUnlinking] = useState<string | null>(null);
  const [linking, setLinking] = useState<string | null>(null);
  const loginMethods = useAuthConfigStore((s) => s.loginMethods);

  const ALL_PROVIDERS = [
    { id: "github", enabled: loginMethods.github_enabled },
    { id: "google", enabled: loginMethods.google_enabled },
    { id: "qq", enabled: loginMethods.qq_enabled },
  ];

  useEffect(() => {
    fetchOAuthLinks()
      .then((data) => {
        setLinks(data.links);
        setHasPassword(data.has_password);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const linkedProviders = new Set(links.map((l) => l.provider));

  const handleUnlink = async (provider: string) => {
    setUnlinking(provider);
    try {
      const result = await unlinkOAuth(provider);
      setLinks(result.links);
      showToast(`已解绑 ${PROVIDER_META[provider]?.label || provider}`, "success");
    } catch (e: any) {
      showToast(e.message || "解绑失败", "error");
    } finally {
      setUnlinking(null);
    }
  };

  const handleLink = async (provider: string) => {
    setLinking(provider);
    try {
      const url = await getOAuthUrl(provider as "github" | "google" | "qq");
      window.location.href = url;
    } catch {
      showToast(`${PROVIDER_META[provider]?.label || provider} 绑定暂不可用`, "error");
      setLinking(null);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {ALL_PROVIDERS.filter((p) => p.enabled || linkedProviders.has(p.id)).map(({ id }) => {
        const meta = PROVIDER_META[id] || { label: id, color: "#666" };
        const linked = linkedProviders.has(id);
        const linkInfo = links.find((l) => l.provider === id);
        const canUnlink = hasPassword || links.length > 1;

        return (
          <div
            key={id}
            className="flex items-center justify-between p-3 rounded-lg border bg-background"
          >
            <div className="flex items-center gap-3">
              <div
                className="h-8 w-8 rounded-lg flex items-center justify-center text-white"
                style={{ backgroundColor: meta.color }}
              >
                <ProviderIcon provider={id} className="h-4 w-4" />
              </div>
              <div>
                <div className="text-sm font-medium">{meta.label}</div>
                {linked && linkInfo?.linked_at && (
                  <div className="text-xs text-muted-foreground">
                    绑定于 {new Date(linkInfo.linked_at).toLocaleDateString("zh-CN")}
                  </div>
                )}
              </div>
            </div>
            {linked ? (
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs"
                disabled={!canUnlink || unlinking === id}
                onClick={() => handleUnlink(id)}
                title={!canUnlink ? "需要至少保留一种登录方式" : undefined}
              >
                {unlinking === id ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  "解绑"
                )}
              </Button>
            ) : (
              <Button
                size="sm"
                className="h-7 text-xs"
                disabled={linking === id}
                onClick={() => handleLink(id)}
                style={{ backgroundColor: meta.color, color: "#fff" }}
              >
                {linking === id ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  "绑定"
                )}
              </Button>
            )}
          </div>
        );
      })}
      {!hasPassword && links.length <= 1 && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          提示：请先设置密码后再解绑第三方登录，确保账号可登录。
        </p>
      )}
    </div>
  );
}

// ── Codex Provider Section ─────────────────────────────────

function CodexProviderSection({
  showToast,
}: {
  showToast: (msg: string, type: "success" | "error") => void;
}) {
  const [status, setStatus] = useState<CodexStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [tokenInput, setTokenInput] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [showFallback, setShowFallback] = useState(false);
  const [showManual, setShowManual] = useState(false);

  // OAuth PKCE Browser Flow state
  const [oauthBusy, setOauthBusy] = useState(false);
  const [oauthState, setOauthState] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const [oauthMode, setOauthMode] = useState<"popup" | "paste" | null>(null);
  const popupRef = useRef<Window | null>(null);
  const popupTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Device Code Flow state
  const [deviceState, setDeviceState] = useState<string | null>(null);
  const [userCode, setUserCode] = useState("");
  const [verificationUrl, setVerificationUrl] = useState("");
  const [authorizing, setAuthorizing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshStatus = useCallback(() => {
    fetchCodexStatus()
      .then(setStatus)
      .catch(() => setStatus({ status: "disconnected", provider: "openai-codex" }));
  }, []);

  useEffect(() => {
    fetchCodexStatus()
      .then(setStatus)
      .catch(() => setStatus({ status: "disconnected", provider: "openai-codex" }))
      .finally(() => setLoading(false));
  }, []);

  // 清理轮询和 popup 监听
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (popupTimerRef.current) clearInterval(popupTimerRef.current);
    };
  }, []);

  // 监听 popup postMessage 回调 (Path A)
  useEffect(() => {
    const handler = (event: MessageEvent) => {
      if (event.origin !== window.location.origin) return;
      if (event.data?.type !== "codex-oauth-callback") return;

      // 清理 popup 监控
      if (popupTimerRef.current) {
        clearInterval(popupTimerRef.current);
        popupTimerRef.current = null;
      }

      if (event.data.error) {
        setOauthBusy(false);
        setOauthMode(null);
        showToast(event.data.error, "error");
        return;
      }

      const { code, state: cbState } = event.data;
      if (code && cbState) {
        codexOAuthExchange(code, cbState)
          .then((result) => {
            setStatus({
              status: "connected",
              provider: "openai-codex",
              account_id: result.account_id,
              plan_type: result.plan_type,
              expires_at: result.expires_at,
              is_active: true,
              has_refresh_token: true,
            });
            showToast("OpenAI Codex 已连接", "success");
          })
          .catch((e: any) => {
            showToast(e.message || "OAuth 交换失败", "error");
          })
          .finally(() => {
            setOauthBusy(false);
            setOauthMode(null);
            setOauthState("");
          });
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, [showToast]);

  // ── OAuth PKCE 浏览器流程 ──────────────────────────────
  const handleOAuthLogin = async () => {
    if (oauthBusy) return;
    setOauthBusy(true);
    setPasteUrl("");

    try {
      // OpenAI Codex public client常见只接受 localhost:1455/auth/callback。
      // 因此仅当当前前端本身跑在 1455 端口时才使用 popup 回调自动模式；
      // 其他场景强制走 paste 模式（不传 redirect_uri）。
      const isCliLocalCallback =
        ["localhost", "127.0.0.1"].includes(window.location.hostname)
        && window.location.port === "1455";
      const redirectUri = isCliLocalCallback
        ? `${window.location.origin}/auth/callback`
        : undefined;

      const data = await codexOAuthStart(redirectUri);
      setOauthState(data.state);
      setOauthMode(data.mode);

      // 打开 popup
      const w = 600, h = 700;
      const left = window.screenX + (window.outerWidth - w) / 2;
      const top = window.screenY + (window.outerHeight - h) / 2;
      const popup = window.open(
        data.authorize_url,
        "codex-oauth",
        `width=${w},height=${h},left=${left},top=${top},toolbar=no,menubar=no`,
      );
      popupRef.current = popup;

      if (data.mode === "popup" && popup) {
        // Path A: 监控 popup 关闭（postMessage 会在回调页处理）
        popupTimerRef.current = setInterval(() => {
          if (popup.closed) {
            if (popupTimerRef.current) {
              clearInterval(popupTimerRef.current);
              popupTimerRef.current = null;
            }
            // 如果 popup 关闭但未收到 postMessage，可能用户手动关闭了
            setTimeout(() => {
              setOauthBusy((busy) => {
                if (busy) {
                  setOauthMode(null);
                  setOauthState("");
                  return false;
                }
                return busy;
              });
            }, 2000);
          }
        }, 500);
      } else {
        // Path B: paste 模式 — popup 会重定向到 localhost (失败)
        // 不自动重置 oauthBusy，等待用户粘贴 URL
      }
    } catch (e: any) {
      setOauthBusy(false);
      setOauthMode(null);
      showToast(e.message || "无法发起 OAuth 登录", "error");
    }
  };

  const handlePasteUrlSubmit = async () => {
    if (!pasteUrl.trim() || !oauthState) return;
    try {
      const url = new URL(pasteUrl.trim());
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state");
      if (!code || !state) {
        showToast("URL 中缺少 code 或 state 参数", "error");
        return;
      }
      const result = await codexOAuthExchange(code, state);
      setStatus({
        status: "connected",
        provider: "openai-codex",
        account_id: result.account_id,
        plan_type: result.plan_type,
        expires_at: result.expires_at,
        is_active: true,
        has_refresh_token: true,
      });
      showToast("OpenAI Codex 已连接", "success");
    } catch (e: any) {
      showToast(e.message || "连接失败", "error");
    } finally {
      setOauthBusy(false);
      setOauthMode(null);
      setOauthState("");
      setPasteUrl("");
    }
  };

  const cancelOAuth = () => {
    if (popupRef.current && !popupRef.current.closed) {
      popupRef.current.close();
    }
    if (popupTimerRef.current) {
      clearInterval(popupTimerRef.current);
      popupTimerRef.current = null;
    }
    setOauthBusy(false);
    setOauthMode(null);
    setOauthState("");
    setPasteUrl("");
  };

  // ── Device Code Flow ──────────────────────────────
  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setAuthorizing(false);
    setDeviceState(null);
    setUserCode("");
    setVerificationUrl("");
  }, []);

  const handleDeviceCodeAuthorize = async () => {
    if (authorizing) return;
    setAuthorizing(true);
    try {
      const data = await codexDeviceCodeStart();
      setUserCode(data.user_code);
      setVerificationUrl(data.verification_url);
      setDeviceState(data.state);

      const interval = Math.max(data.interval, 3) * 1000;
      pollRef.current = setInterval(async () => {
        try {
          const result = await codexDeviceCodePoll(data.state);
          if (result.status === "connected") {
            stopPolling();
            refreshStatus();
            showToast("OpenAI Codex 已连接", "success");
          }
        } catch {
          // 轮询错误不中断
        }
      }, interval);

      setTimeout(() => {
        if (pollRef.current) {
          stopPolling();
          showToast("设备码已过期，请重试", "error");
        }
      }, 15 * 60 * 1000);
    } catch (e: any) {
      setAuthorizing(false);
      showToast(e.message || "无法发起设备码登录", "error");
    }
  };

  // ── Paste Token ──────────────────────────────
  const handleConnect = async () => {
    if (!tokenInput.trim() || connecting) return;
    setConnecting(true);
    try {
      const parsed = JSON.parse(tokenInput.trim());
      const result = await connectCodex(parsed);
      setStatus({
        status: "connected",
        provider: "openai-codex",
        account_id: result.account_id,
        plan_type: result.plan_type,
        expires_at: result.expires_at,
        is_active: true,
        has_refresh_token: true,
      });
      setTokenInput("");
      showToast("OpenAI Codex 已连接", "success");
    } catch (e: any) {
      if (e instanceof SyntaxError) {
        showToast("JSON 格式无效，请粘贴完整的 auth.json 内容", "error");
      } else {
        showToast(e.message || "连接失败", "error");
      }
    } finally {
      setConnecting(false);
    }
  };

  const handleDisconnect = async () => {
    if (disconnecting) return;
    setDisconnecting(true);
    try {
      await disconnectCodex();
      setStatus({ status: "disconnected", provider: "openai-codex" });
      showToast("已断开 OpenAI Codex", "success");
    } catch (e: any) {
      showToast(e.message || "断开失败", "error");
    } finally {
      setDisconnecting(false);
    }
  };

  const handleRefresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const result = await refreshCodexToken();
      setStatus((prev) =>
        prev ? { ...prev, status: "connected", expires_at: result.expires_at } : prev
      );
      showToast("Token 已刷新", "success");
    } catch (e: any) {
      showToast(e.message || "刷新失败", "error");
    } finally {
      setRefreshing(false);
    }
  };

  // ── Render ──────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const isConnected = status?.status === "connected";
  const isExpired = status?.status === "expired";

  if (isConnected || isExpired) {
    return (
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <div
            className={`h-2 w-2 rounded-full ${isExpired ? "bg-amber-500" : "bg-green-500"}`}
          />
          <span className="text-sm font-medium">
            {isExpired ? "Token 已过期" : "已连接"}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-y-2 text-sm">
          {status?.account_id && (
            <>
              <span className="text-muted-foreground">账户</span>
              <span className="font-mono text-xs truncate">{status.account_id}</span>
            </>
          )}
          {status?.plan_type && (
            <>
              <span className="text-muted-foreground">订阅</span>
              <span className="capitalize">{status.plan_type}</span>
            </>
          )}
          {status?.expires_at && (
            <>
              <span className="text-muted-foreground">有效期至</span>
              <span className="text-xs">
                {new Date(status.expires_at).toLocaleString("zh-CN")}
              </span>
            </>
          )}
        </div>
        <div className="flex gap-2 pt-1">
          {status?.has_refresh_token && (
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              disabled={refreshing}
              onClick={handleRefresh}
            >
              {refreshing ? <Loader2 className="h-3 w-3 animate-spin" /> : "刷新 Token"}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs text-red-600 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950"
            disabled={disconnecting}
            onClick={handleDisconnect}
          >
            {disconnecting ? <Loader2 className="h-3 w-3 animate-spin" /> : "断开连接"}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground leading-relaxed">
        使用 ChatGPT Plus/Pro 订阅访问 Codex 模型，无需 API Key。
      </p>

      {/* OAuth PKCE 浏览器登录（主要方式） */}
      {!oauthBusy ? (
        <Button
          onClick={handleOAuthLogin}
          size="sm"
          className="w-full h-9 text-sm font-medium"
          style={{ backgroundColor: "var(--em-primary)", color: "#fff" }}
          disabled={authorizing}
        >
          使用 ChatGPT 账号登录
        </Button>
      ) : (
        <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-4">
          {oauthMode === "popup" ? (
            <div className="text-center space-y-2">
              <Loader2 className="h-6 w-6 mx-auto animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                请在弹出窗口中完成 OpenAI 登录...
              </p>
              <p className="text-xs text-muted-foreground">
                授权完成后此页面会自动更新
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="text-center space-y-2">
                <p className="text-sm text-muted-foreground">
                  在弹出窗口中完成 OpenAI 登录后，请复制地址栏中的完整 URL 粘贴到下方：
                </p>
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  提示：登录完成后页面可能显示无法访问，这是正常的，请直接复制地址栏 URL
                </p>
              </div>
              <input
                type="text"
                value={pasteUrl}
                onChange={(e) => setPasteUrl(e.target.value)}
                className="w-full h-9 px-3 rounded-md border border-input bg-background text-xs font-mono outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
                placeholder="http://localhost:1455/auth/callback?code=...&state=..."
              />
              <Button
                onClick={handlePasteUrlSubmit}
                disabled={!pasteUrl.trim()}
                size="sm"
                className="w-full h-8 text-xs"
                style={{ backgroundColor: "var(--em-primary)", color: "#fff" }}
              >
                确认连接
              </Button>
            </div>
          )}
          <div className="flex justify-center">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              onClick={cancelOAuth}
            >
              取消
            </Button>
          </div>
        </div>
      )}

      {/* 备选连接方式（折叠） */}
      <div className="pt-1">
        <button
          onClick={() => setShowFallback(!showFallback)}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          {showFallback ? "▾ 收起备选方式" : "▸ 其他连接方式"}
        </button>
        {showFallback && (
          <div className="mt-3 space-y-4">
            {/* Device Code 流程 */}
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">设备码登录</p>
              {!authorizing ? (
                <Button
                  onClick={handleDeviceCodeAuthorize}
                  size="sm"
                  className="w-full h-8 text-xs"
                  variant="outline"
                  disabled={oauthBusy}
                >
                  使用设备码登录
                </Button>
              ) : (
                <div className="space-y-3 rounded-lg border border-border bg-muted/30 p-3">
                  <div className="text-center space-y-2">
                    <p className="text-xs text-muted-foreground">
                      请在浏览器中打开以下链接并输入验证码：
                    </p>
                    <a
                      href={verificationUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-medium underline"
                      style={{ color: "var(--em-primary)" }}
                    >
                      {verificationUrl}
                    </a>
                    <div className="flex items-center justify-center gap-2 pt-1">
                      <span
                        className="font-mono text-xl font-bold tracking-widest select-all px-3 py-1.5 rounded-md border border-border bg-background cursor-pointer"
                        title="点击复制"
                        onClick={() => {
                          navigator.clipboard.writeText(userCode);
                          showToast("验证码已复制", "success");
                        }}
                      >
                        {userCode}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      点击验证码可复制
                    </p>
                  </div>
                  <div className="flex items-center justify-center gap-3">
                    <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />
                    <span className="text-xs text-muted-foreground">等待授权中...</span>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-xs"
                      onClick={stopPolling}
                    >
                      取消
                    </Button>
                  </div>
                </div>
              )}
            </div>

            {/* 手动粘贴 Token */}
            <div className="space-y-2">
              <button
                onClick={() => setShowManual(!showManual)}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                {showManual ? "▾ 收起手动粘贴" : "▸ 手动粘贴 Token"}
              </button>
              {showManual && (
                <div className="mt-2 space-y-2">
                  <div className="text-xs text-muted-foreground space-y-1">
                    <p>1. 在终端运行 <code className="px-1 py-0.5 rounded bg-muted font-mono">codex login</code></p>
                    <p>2. 复制 <code className="px-1 py-0.5 rounded bg-muted font-mono">~/.codex/auth.json</code> 内容</p>
                    <p>3. 粘贴到下方输入框</p>
                  </div>
                  <textarea
                    value={tokenInput}
                    onChange={(e) => setTokenInput(e.target.value)}
                    rows={4}
                    className="w-full px-3 py-2 rounded-md border border-input bg-background text-xs font-mono outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow resize-none"
                    placeholder='{"token": "eyJ...", "refresh_token": "rt_...", ...}'
                  />
                  <Button
                    onClick={handleConnect}
                    disabled={!tokenInput.trim() || connecting}
                    size="sm"
                    className="h-8 px-4 text-xs"
                    variant="outline"
                  >
                    {connecting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
                    粘贴连接
                  </Button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Section Card ───────────────────────────────────────────

function SectionCard({
  title,
  icon: Icon,
  children,
  index,
}: {
  title: string;
  icon: typeof User;
  children: React.ReactNode;
  index: number;
}) {
  return (
    <motion.div
      custom={index}
      variants={cardVariants}
      initial="hidden"
      animate="visible"
      className="rounded-xl border border-border bg-card p-5 shadow-sm"
    >
      <div className="flex items-center gap-2 mb-4">
        <div
          className="h-8 w-8 rounded-lg flex items-center justify-center"
          style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
        >
          <Icon className="h-4 w-4" style={{ color: "var(--em-primary)" }} />
        </div>
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      {children}
    </motion.div>
  );
}

// ── Main Page ──────────────────────────────────────────────

export function ProfilePage() {
  const user = useAuthStore((s) => s.user);

  // toast
  const [toast, setToast] = useState<{
    message: string;
    type: "success" | "error";
  } | null>(null);
  const showToast = useCallback(
    (message: string, type: "success" | "error") =>
      setToast({ message, type }),
    [],
  );

  // display name
  const [displayName, setDisplayName] = useState(user?.displayName || "");
  const [savingName, setSavingName] = useState(false);
  const nameChanged = displayName !== (user?.displayName || "");

  // avatar
  const [uploadingAvatar, setUploadingAvatar] = useState(false);

  // email
  const [newEmail, setNewEmail] = useState("");
  const [emailPassword, setEmailPassword] = useState("");
  const [savingEmail, setSavingEmail] = useState(false);
  const [showEmailPwd, setShowEmailPwd] = useState(false);

  // password
  const [oldPwd, setOldPwd] = useState("");
  const [newPwd, setNewPwd] = useState("");
  const [confirmPwd, setConfirmPwd] = useState("");
  const [savingPwd, setSavingPwd] = useState(false);
  const [showOldPwd, setShowOldPwd] = useState(false);
  const [showNewPwd, setShowNewPwd] = useState(false);

  // workspace
  const [wsUsage, setWsUsage] = useState<WorkspaceUsage | null>(null);
  useEffect(() => {
    fetchMyWorkspaceUsage().then(setWsUsage).catch(() => {});
  }, []);

  if (!user) {
    return null;
  }

  // handlers
  const handleSaveName = async () => {
    if (!nameChanged || savingName) return;
    setSavingName(true);
    try {
      await updateProfile({ display_name: displayName });
      showToast("用户名已更新", "success");
    } catch (e: any) {
      showToast(e.message || "更新失败", "error");
    } finally {
      setSavingName(false);
    }
  };

  const handleAvatarUpload = async (file: File) => {
    setUploadingAvatar(true);
    try {
      await uploadAvatar(file);
      showToast("头像已更新", "success");
    } catch (e: any) {
      showToast(e.message || "上传失败", "error");
    } finally {
      setUploadingAvatar(false);
    }
  };

  const handleChangeEmail = async () => {
    if (!newEmail || !emailPassword || savingEmail) return;
    setSavingEmail(true);
    try {
      await changeEmail(newEmail, emailPassword);
      showToast("邮箱已更新", "success");
      setNewEmail("");
      setEmailPassword("");
    } catch (e: any) {
      showToast(e.message || "修改失败", "error");
    } finally {
      setSavingEmail(false);
    }
  };

  const handleChangePassword = async () => {
    if (!oldPwd || !newPwd || savingPwd) return;
    if (newPwd !== confirmPwd) {
      showToast("两次输入的新密码不一致", "error");
      return;
    }
    if (newPwd.length < 8) {
      showToast("新密码至少需要 8 个字符", "error");
      return;
    }
    setSavingPwd(true);
    try {
      await changePassword(oldPwd, newPwd);
      showToast("密码已更新", "success");
      setOldPwd("");
      setNewPwd("");
      setConfirmPwd("");
    } catch (e: any) {
      showToast(e.message || "修改失败", "error");
    } finally {
      setSavingPwd(false);
    }
  };

  const wsPct =
    wsUsage && wsUsage.max_size_mb > 0
      ? Math.min((wsUsage.size_mb / wsUsage.max_size_mb) * 100, 100)
      : 0;

  return (
    <div className="h-full">
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onClose={() => setToast(null)}
        />
      )}

      <div className="px-5 py-5 space-y-5">
        {/* Profile Header Card */}
        <motion.div
          variants={cardVariants}
          custom={0}
          initial="hidden"
          animate="visible"
          className="relative overflow-hidden rounded-xl border border-border bg-card p-5 shadow-sm"
        >
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              background: `radial-gradient(ellipse at 0% -30%, var(--em-primary-alpha-10) 0%, transparent 60%),
                            radial-gradient(ellipse at 100% 120%, var(--em-primary-alpha-06) 0%, transparent 50%)`,
            }}
          />
          <div className="relative flex items-center gap-5">
            <ProfileAvatar
              src={user.avatarUrl}
              name={user.displayName || user.email}
              onUpload={handleAvatarUpload}
              uploading={uploadingAvatar}
            />
            <div className="min-w-0 flex-1">
              <h2 className="text-lg font-semibold truncate">
                {user.displayName || user.email.split("@")[0]}
              </h2>
              <p className="text-sm text-muted-foreground truncate">
                {user.email}
              </p>
              <div className="flex items-center gap-2 mt-2">
                <span
                  className="inline-flex items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full"
                  style={{
                    backgroundColor: "var(--em-primary-alpha-10)",
                    color: "var(--em-primary)",
                  }}
                >
                  <Shield className="h-3 w-3" />
                  {user.role === "admin" ? "管理员" : "用户"}
                </span>
                {wsUsage && (
                  <span className="text-xs text-muted-foreground">
                    空间 {wsUsage.size_mb.toFixed(1)} / {wsUsage.max_size_mb} MB
                  </span>
                )}
              </div>
              {wsUsage && (
                <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden max-w-[200px]">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${
                      wsUsage.over_size || wsUsage.over_files
                        ? "bg-red-500"
                        : wsPct > 80
                          ? "bg-amber-500"
                          : "bg-[var(--em-primary)]"
                    }`}
                    style={{ width: `${wsPct}%` }}
                  />
                </div>
              )}
            </div>
          </div>
        </motion.div>

        {/* Username */}
        <SectionCard title="用户名" icon={User} index={1}>
          <div className="flex gap-3">
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={100}
              className="flex-1 h-9 px-3 rounded-md border border-input bg-background text-sm outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
              placeholder="输入用户名"
            />
            <Button
              onClick={handleSaveName}
              disabled={!nameChanged || savingName}
              size="sm"
              className="h-9 px-4"
              style={
                nameChanged
                  ? { backgroundColor: "var(--em-primary)", color: "#fff" }
                  : undefined
              }
            >
              {savingName ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                "保存"
              )}
            </Button>
          </div>
        </SectionCard>

        {/* Email */}
        <SectionCard title="邮箱" icon={Mail} index={2}>
          <p className="text-xs text-muted-foreground mb-3">
            当前邮箱: <span className="font-medium text-foreground">{user.email}</span>
          </p>
          <div className="space-y-3">
            <input
              type="email"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
              placeholder="新邮箱地址"
            />
            <div className="relative">
              <input
                type={showEmailPwd ? "text" : "password"}
                value={emailPassword}
                onChange={(e) => setEmailPassword(e.target.value)}
                className="w-full h-9 px-3 pr-10 rounded-md border border-input bg-background text-sm outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
                placeholder="输入当前密码以验证"
              />
              <button
                type="button"
                onClick={() => setShowEmailPwd(!showEmailPwd)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showEmailPwd ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
            <Button
              onClick={handleChangeEmail}
              disabled={!newEmail || !emailPassword || savingEmail}
              size="sm"
              className="h-9 px-4"
              style={
                newEmail && emailPassword
                  ? { backgroundColor: "var(--em-primary)", color: "#fff" }
                  : undefined
              }
            >
              {savingEmail ? (
                <Loader2 className="h-4 w-4 animate-spin mr-1" />
              ) : null}
              修改邮箱
            </Button>
          </div>
        </SectionCard>

        {/* Password */}
        <SectionCard title="密码" icon={KeyRound} index={3}>
          {!user.hasPassword ? (
            <p className="text-xs text-muted-foreground">
              当前账号通过第三方登录注册，尚未设置密码。请在登录设置中先设置密码。
            </p>
          ) : (
            <div className="space-y-3">
              <div className="relative">
                <input
                  type={showOldPwd ? "text" : "password"}
                  value={oldPwd}
                  onChange={(e) => setOldPwd(e.target.value)}
                  className="w-full h-9 px-3 pr-10 rounded-md border border-input bg-background text-sm outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
                  placeholder="当前密码"
                />
                <button
                  type="button"
                  onClick={() => setShowOldPwd(!showOldPwd)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showOldPwd ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
              <div className="relative">
                <input
                  type={showNewPwd ? "text" : "password"}
                  value={newPwd}
                  onChange={(e) => setNewPwd(e.target.value)}
                  className="w-full h-9 px-3 pr-10 rounded-md border border-input bg-background text-sm outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
                  placeholder="新密码（至少 8 位）"
                />
                <button
                  type="button"
                  onClick={() => setShowNewPwd(!showNewPwd)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showNewPwd ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
              <input
                type="password"
                value={confirmPwd}
                onChange={(e) => setConfirmPwd(e.target.value)}
                className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
                placeholder="确认新密码"
              />
              <Button
                onClick={handleChangePassword}
                disabled={!oldPwd || !newPwd || !confirmPwd || savingPwd}
                size="sm"
                className="h-9 px-4"
                style={
                  oldPwd && newPwd && confirmPwd
                    ? { backgroundColor: "var(--em-primary)", color: "#fff" }
                    : undefined
                }
              >
                {savingPwd ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                ) : null}
                修改密码
              </Button>
            </div>
          )}
        </SectionCard>

        {/* OAuth Links */}
        <SectionCard title="登录方式" icon={KeyRound} index={4}>
          <OAuthLinksSection showToast={showToast} />
        </SectionCard>

        {/* Account Info */}
        <SectionCard title="账户信息" icon={Shield} index={5}>
          <div className="grid grid-cols-2 gap-y-3 text-sm">
            <span className="text-muted-foreground">账户 ID</span>
            <span className="font-mono text-xs truncate">{user.id}</span>
            <span className="text-muted-foreground">角色</span>
            <span>{user.role === "admin" ? "管理员" : "用户"}</span>
            <span className="text-muted-foreground">注册时间</span>
            <span>
              {new Date(user.createdAt).toLocaleDateString("zh-CN", {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </span>
            {wsUsage && (
              <>
                <span className="text-muted-foreground">文件数</span>
                <span>
                  {wsUsage.file_count} / {wsUsage.max_files}
                </span>
              </>
            )}
          </div>
        </SectionCard>

        <div className="h-4" />
      </div>
    </div>
  );
}
