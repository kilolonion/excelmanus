"use client";

import { useState, useCallback, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2, Github, Mail, Eye, EyeOff, AlertCircle, X, Clock } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Suspense } from "react";
import { Button } from "@/components/ui/button";
import { login, getOAuthUrl } from "@/lib/auth-api";
import { proxyAvatarUrl } from "@/lib/api";
import { useRecentAccountsStore, canAutoLogin, type RecentAccount } from "@/stores/recent-accounts-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";

const cardVariants = {
  hidden: { opacity: 0, y: 24 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: "easeOut" as const } },
};

function AccountAvatar({ account }: { account: RecentAccount }) {
  const [failed, setFailed] = useState(false);
  const proxied = proxyAvatarUrl(account.avatarUrl);
  if (proxied && !failed) {
    return (
      <img
        src={proxied}
        alt=""
        className="h-8 w-8 rounded-full flex-shrink-0"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <span
      className="h-8 w-8 rounded-full flex items-center justify-center text-sm font-medium text-white flex-shrink-0"
      style={{ backgroundColor: "var(--em-primary)" }}
    >
      {(account.displayName || account.email)[0]?.toUpperCase() || "U"}
    </span>
  );
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const recentAccounts = useRecentAccountsStore((s) => s.accounts);
  const removeAccount = useRecentAccountsStore((s) => s.removeAccount);
  const recordLogin = useRecentAccountsStore((s) => s.recordLogin);
  const loginMethods = useAuthConfigStore((s) => s.loginMethods);
  const githubEnabled = loginMethods.github_enabled;
  const googleEnabled = loginMethods.google_enabled;
  const qqEnabled = loginMethods.qq_enabled;
  const showOAuthSection = githubEnabled || googleEnabled || qqEnabled;

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<string | null>(null);
  const [showRecentList, setShowRecentList] = useState(true);
  const [rememberMe, setRememberMe] = useState(false);
  const [autoLoggingIn, setAutoLoggingIn] = useState(false);

  useEffect(() => {
    const prefill = searchParams.get("email");
    if (prefill) {
      setEmail(prefill);
      setShowRecentList(false);
    }
  }, [searchParams]);

  useEffect(() => {
    const handlePageShow = (e: PageTransitionEvent) => {
      if (e.persisted) setOauthLoading(null);
    };
    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, []);

  // 自动登录逻辑：检查最近账号是否有保存的密码且未过期
  useEffect(() => {
    // 如果有 email 参数，不自动登录
    if (searchParams.get("email")) return;
    // 如果已经有最近账号，尝试自动登录第一个可用的
    if (recentAccounts.length > 0 && !email && !autoLoggingIn) {
      const account = recentAccounts.find(canAutoLogin);
      if (account && account.savedPassword) {
        setAutoLoggingIn(true);
        setEmail(account.email);
        setPassword(account.savedPassword);
        setShowRecentList(false);
        // 延迟执行自动登录，确保状态已更新
        setTimeout(async () => {
          try {
            setError("");
            await login(account.email, account.savedPassword!);
            router.push("/");
          } catch (err) {
            // 自动登录失败，清除保存的密码并显示错误
            console.error("自动登录失败:", err);
            setAutoLoggingIn(false);
            setEmail(account.email);
            setPassword("");
            setError("自动登录失败，请重新输入密码");
          }
        }, 100);
      }
    }
  }, [recentAccounts, searchParams, email, autoLoggingIn, router]);

  const canSubmit = email.trim().length > 0 && password.length > 0 && !loading;

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(email.trim(), password);
      // 登录成功后，记录到最近账号（如果选择了记住我，保存密码）
      recordLogin({
        email: email.trim(),
        displayName: "",
        avatarUrl: null,
        password: rememberMe ? password : undefined,
        rememberMe,
      });
      router.push("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }, [email, password, router, rememberMe, recordLogin]);

  const handleOAuth = useCallback(async (provider: "github" | "google" | "qq") => {
    setOauthLoading(provider);
    setError("");
    try {
      const url = await getOAuthUrl(provider);
      window.location.href = url;
    } catch {
      const names = { github: "GitHub", google: "Google", qq: "QQ" };
      setError(`${names[provider]} 登录暂不可用`);
      setOauthLoading(null);
    }
  }, []);

  const handlePickAccount = (account: RecentAccount) => {
    setEmail(account.email);
    setShowRecentList(false);
    setError("");
    setTimeout(() => {
      document.getElementById("password")?.focus();
    }, 50);
  };

  const showRecent = showRecentList && recentAccounts.length > 0 && !email;

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-background to-muted/30 px-4">
      <motion.div
        className="w-full max-w-[400px] space-y-6"
        variants={cardVariants}
        initial="hidden"
        animate="visible"
      >
        {/* Header */}
        <div className="text-center space-y-2">
          <img src="/logo.svg" alt="ExcelManus" className="h-12 w-auto mx-auto" />
          <h1 className="text-2xl font-bold tracking-tight">登录 ExcelManus</h1>
          <p className="text-muted-foreground text-sm">基于大语言模型的 Excel 智能代理</p>
        </div>

        {/* Error */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="rounded-lg bg-destructive/10 border border-destructive/20 px-3 py-2.5 text-sm text-destructive flex items-start gap-2"
            >
              <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
              <span>{error}</span>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Recent accounts */}
        <AnimatePresence>
          {showRecent && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              exit={{ opacity: 0, height: 0 }}
              className="space-y-2"
            >
              <p className="text-xs text-muted-foreground flex items-center gap-1.5 px-1">
                <Clock className="h-3 w-3" />
                选择账号快捷登录
              </p>
              <div className="rounded-lg border border-border overflow-hidden">
                {recentAccounts.map((account, i) => (
                  <div
                    key={account.email}
                    role="button"
                    tabIndex={0}
                    onClick={() => handlePickAccount(account)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        handlePickAccount(account);
                      }
                    }}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-muted/60 transition-colors group cursor-pointer ${
                      i > 0 ? "border-t border-border" : ""
                    }`}
                  >
                    <AccountAvatar account={account} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium truncate">
                        {account.displayName || account.email.split("@")[0]}
                      </p>
                      <p className="text-xs text-muted-foreground truncate">{account.email}</p>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        removeAccount(account.email);
                      }}
                      className="p-1 rounded hover:bg-destructive/20 hover:text-destructive text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 touch-show"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
              <button
                type="button"
                onClick={() => setShowRecentList(false)}
                className="text-xs text-[var(--em-primary)] hover:underline px-1"
              >
                使用其他账号登录
              </button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Form - shown when no recent accounts picked or user wants manual entry */}
        {!showRecent && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label className="block text-sm font-medium" htmlFor="email">邮箱</label>
              <div className="relative">
                <input
                  id="email"
                  type="email"
                  required
                  autoFocus={!email}
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full h-11 rounded-lg border border-border bg-background px-3 pr-9 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50"
                  placeholder="you@example.com"
                />
                {email && recentAccounts.length > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      setEmail("");
                      setShowRecentList(true);
                    }}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium" htmlFor="password">密码</label>
                <Link
                  href="/forgot-password"
                  className="text-xs text-muted-foreground hover:text-[var(--em-primary)] transition-colors"
                >
                  忘记密码？
                </Link>
              </div>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? "text" : "password"}
                  required
                  autoFocus={!!email}
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full h-11 rounded-lg border border-border bg-background px-3 pr-10 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50"
                  placeholder="输入密码"
                />
                <button
                  type="button"
                  tabIndex={-1}
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {/* 记住我 */}
            <div className="flex items-center">
              <label
                htmlFor="remember-me"
                className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer select-none"
              >
                <input
                  id="remember-me"
                  type="checkbox"
                  checked={rememberMe}
                  onChange={(e) => setRememberMe(e.target.checked)}
                  className="w-4 h-4 rounded border-border text-[var(--em-primary)] focus:ring-[var(--em-primary)] focus:ring-offset-0"
                />
                7天内免密登录
              </label>
            </div>

            <Button
              type="submit"
              disabled={!canSubmit}
              className="w-full h-11 text-white font-medium transition-all"
              style={{ backgroundColor: "var(--em-primary)" }}
            >
              {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "登录"}
            </Button>
          </form>
        )}

        {/* Divider */}
        {showOAuthSection && (
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t border-border" />
            </div>
            <div className="relative flex justify-center text-xs">
              <span className="bg-gradient-to-b from-background to-muted/30 px-3 text-muted-foreground">
                或使用第三方账号
              </span>
            </div>
          </div>
        )}

        {/* OAuth */}
        {showOAuthSection && (
          <div className={`grid gap-3 ${[githubEnabled, googleEnabled, qqEnabled].filter(Boolean).length >= 2 ? "grid-cols-2" : "grid-cols-1"}`}>
            {githubEnabled && (
              <Button
                variant="outline"
                className="h-11 font-normal"
                disabled={oauthLoading !== null}
                onClick={() => handleOAuth("github")}
              >
                {oauthLoading === "github" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <Github className="h-4 w-4 mr-2" />
                    GitHub
                  </>
                )}
              </Button>
            )}
            {googleEnabled && (
              <Button
                variant="outline"
                className="h-11 font-normal"
                disabled={oauthLoading !== null}
                onClick={() => handleOAuth("google")}
              >
                {oauthLoading === "google" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <Mail className="h-4 w-4 mr-2" />
                    Google
                  </>
                )}
              </Button>
            )}
            {qqEnabled && (
              <Button
                variant="outline"
                className="h-11 font-normal"
                disabled={oauthLoading !== null}
                onClick={() => handleOAuth("qq")}
              >
                {oauthLoading === "qq" ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <svg className="h-4 w-4 mr-2" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 13.19c-.17.45-.63.84-1.33 1.15.06.22.1.49.1.68 0 .84-.96 1.53-2.14 1.53-.56 0-1.07-.15-1.45-.41-.38.26-.89.41-1.45.41-1.18 0-2.14-.69-2.14-1.53 0-.19.04-.46.1-.68-.7-.31-1.16-.7-1.33-1.15-.07-.19 0-.29.09-.29.14 0 .38.24.62.49.04-.63.22-1.56.68-2.47C9.02 10.78 10.08 8 12 8s2.98 2.78 3.31 4.92c.46.91.64 1.84.68 2.47.24-.25.48-.49.62-.49.09 0 .16.1.03.29z"/></svg>
                    QQ
                  </>
                )}
              </Button>
            )}
          </div>
        )}

        {/* Footer */}
        <p className="text-center text-sm text-muted-foreground">
          还没有账号？{" "}
          <Link href="/register" className="text-[var(--em-primary)] hover:underline font-medium">
            注册
          </Link>
        </p>
      </motion.div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
