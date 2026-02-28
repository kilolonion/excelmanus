"use client";

import { useState, useCallback, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2, Github, Mail, Eye, EyeOff, AlertCircle, X, Clock, Shield, FileSpreadsheet, Sparkles, Bot, BarChart3 } from "lucide-react";
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

const featureVariants = {
  hidden: { opacity: 0, x: -20 },
  visible: (i: number) => ({
    opacity: 1,
    x: 0,
    transition: { duration: 0.4, delay: 0.2 + i * 0.1, ease: "easeOut" as const },
  }),
};

const FEATURES = [
  { icon: FileSpreadsheet, title: "智能读写", desc: "自然语言驱动 Excel 操作" },
  { icon: BarChart3, title: "数据分析", desc: "自动洞察趋势与异常" },
  { icon: Bot, title: "AI 代理", desc: "多步骤任务自主规划执行" },
  { icon: Sparkles, title: "公式生成", desc: "用自然语言描述即可生成" },
];

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
  const skipAutoLoginAfterLogout = useState(() => {
    if (typeof window === "undefined") return false;
    const shouldSkip = sessionStorage.getItem("suppress-auto-login") === "1";
    if (shouldSkip) {
      sessionStorage.removeItem("suppress-auto-login");
    }
    return shouldSkip;
  })[0];
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
  const [oauthAgreed, setOauthAgreed] = useState(false);

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
    // 用户刚主动退出：本次页面生命周期内禁止自动登录
    if (skipAutoLoginAfterLogout) {
      return;
    }
    // 如果已经有最近账号，尝试自动登录第一个可用的
    if (recentAccounts.length > 0 && !email && !autoLoggingIn) {
      const account = recentAccounts.find(canAutoLogin);
      if (account && account.savedPassword) {
        setAutoLoggingIn(true);
        setEmail(account.email);
        setPassword(account.savedPassword);
        setShowRecentList(false);
        setTimeout(async () => {
          try {
            setError("");
            await login(account.email, account.savedPassword!);
            router.push("/");
          } catch (err) {
            console.error("自动登录失败:", err);
            setAutoLoggingIn(false);
            setEmail(account.email);
            setPassword("");
            setError("自动登录失败，请重新输入密码");
          }
        }, 100);
      }
    }
  }, [recentAccounts, searchParams, email, autoLoggingIn, router, skipAutoLoginAfterLogout]);

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
    if (!oauthAgreed) {
      setError("请先同意用户服务协议和隐私政策");
      return;
    }
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
  }, [oauthAgreed]);

  const handlePickAccount = useCallback(async (account: RecentAccount) => {
    setError("");
    // 如果账号有保存的密码且未过期，直接自动登录
    if (canAutoLogin(account) && account.savedPassword) {
      setAutoLoggingIn(true);
      setEmail(account.email);
      setShowRecentList(false);
      try {
        await login(account.email, account.savedPassword);
        router.push("/");
      } catch (err) {
        console.error("快捷登录失败:", err);
        setAutoLoggingIn(false);
        setEmail(account.email);
        setPassword("");
        setShowRecentList(false);
        setError("自动登录失败，请重新输入密码");
        setTimeout(() => document.getElementById("password")?.focus(), 50);
      }
      return;
    }
    // 没有保存密码，填充邮箱并跳转到密码输入
    setEmail(account.email);
    setShowRecentList(false);
    setTimeout(() => document.getElementById("password")?.focus(), 50);
  }, [router]);

  const showRecent = showRecentList && recentAccounts.length > 0 && !email;

  return (
    <div className="h-screen flex bg-gradient-to-b from-background to-muted/30 relative overflow-hidden">
      {/* Decorative background orbs */}
      <div className="auth-bg-orb auth-bg-orb-1" />
      <div className="auth-bg-orb auth-bg-orb-2" />

      {/* ── Left: Brand showcase (desktop only) ── */}
      <div className="hidden lg:flex lg:w-[45%] xl:w-[48%] relative z-10 flex-col justify-center px-12 xl:px-16">
        {/* Subtle left-panel background */}
        <div className="absolute inset-0 login-left-bg" />
        <motion.div
          initial={{ opacity: 0, x: -30 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          className="max-w-md relative"
        >
          <div className="flex items-center gap-3.5 mb-10">
            <div className="w-11 h-11 rounded-xl bg-[var(--em-primary)] flex items-center justify-center shadow-md">
              <img src="/brand-icon.svg" alt="" className="h-7 w-7 brightness-0 invert" />
            </div>
            <span className="text-2xl font-bold tracking-tight">ExcelManus</span>
          </div>

          <h2 className="text-3xl xl:text-4xl font-bold tracking-tight leading-tight mb-4">
            让 AI 成为你的<br />
            <span className="login-brand-gradient">Excel 超能力</span>
          </h2>
          <p className="text-muted-foreground text-base leading-relaxed mb-10">
            基于大语言模型的智能代理，用自然语言完成复杂 Excel 任务。<br className="hidden xl:inline" />
            无需公式，无需 VBA，只需描述你的需求。
          </p>

          <div className="grid grid-cols-2 gap-3.5">
            {FEATURES.map((f, i) => (
              <motion.div
                key={f.title}
                custom={i}
                variants={featureVariants}
                initial="hidden"
                animate="visible"
                className="flex items-start gap-3 p-3.5 rounded-xl login-feature-card transition-all duration-200"
              >
                <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-[var(--em-primary-alpha-15)] flex items-center justify-center">
                  <f.icon className="h-[18px] w-[18px] text-[var(--em-primary)]" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-foreground">{f.title}</p>
                  <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{f.desc}</p>
                </div>
              </motion.div>
            ))}
          </div>

          <div className="mt-12 flex items-center gap-6 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
              开源免费
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
              支持私有部署
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              多模型兼容
            </span>
          </div>
        </motion.div>
      </div>

      {/* ── Right: Login form ── */}
      <div className="flex-1 flex items-center justify-center px-4 py-8 overflow-y-auto relative z-10">
        <motion.div
          className="w-full max-w-[420px] auth-card"
          variants={cardVariants}
          initial="hidden"
          animate="visible"
        >
          <div className="space-y-6">
          {/* Header */}
          <div className="text-center space-y-3">
            <div className="auth-logo-glow inline-block lg:hidden">
              <img src="/logo.svg" alt="ExcelManus" className="h-14 w-auto mx-auto relative" />
            </div>
            <div>
              <h1 className="text-2xl font-bold tracking-tight">登录 ExcelManus</h1>
              <p className="text-muted-foreground text-sm mt-1">基于大语言模型的 Excel 智能代理</p>
            </div>
          </div>

          {/* Auto-login in progress — show spinner instead of form */}
          {autoLoggingIn && !error && (
            <div className="flex flex-col items-center gap-3 py-6">
              <Loader2 className="h-6 w-6 animate-spin text-[var(--em-primary)]" />
              <p className="text-sm text-muted-foreground">正在自动登录…</p>
            </div>
          )}

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
            {showRecent && !autoLoggingIn && (
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
                <div className="rounded-xl border border-border/60 overflow-hidden bg-background/50">
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
                      className={`w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-[var(--em-primary-alpha-06)] transition-colors group cursor-pointer ${
                        i > 0 ? "border-t border-border/60" : ""
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
          {!showRecent && !autoLoggingIn && (
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
                    className="auth-input w-full h-11 rounded-lg border border-border bg-background px-3 pr-9 text-sm focus:outline-none placeholder:text-muted-foreground/50"
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
                    className="auth-input w-full h-11 rounded-lg border border-border bg-background px-3 pr-10 text-sm focus:outline-none placeholder:text-muted-foreground/50"
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
                  className="flex items-center gap-2.5 text-sm text-muted-foreground cursor-pointer select-none hover:text-foreground transition-colors"
                >
                  <input
                    id="remember-me"
                    type="checkbox"
                    checked={rememberMe}
                    onChange={(e) => setRememberMe(e.target.checked)}
                    className="auth-checkbox"
                  />
                  <span className="flex items-center gap-1.5">
                    <Shield className="h-3.5 w-3.5" />
                    7天内免密登录
                  </span>
                </label>
              </div>

              <Button
                type="submit"
                disabled={!canSubmit}
                className="auth-btn-primary w-full h-11 text-white font-medium border-0"
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "登录"}
              </Button>
            </form>
          )}

          {/* Divider */}
          {showOAuthSection && !autoLoggingIn && (
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <div className="w-full auth-divider-line" />
              </div>
              <div className="relative flex justify-center text-xs">
                <span className="bg-transparent px-3 text-muted-foreground backdrop-blur-sm">
                  或使用第三方账号
                </span>
              </div>
            </div>
          )}

          {/* OAuth agreement */}
          {showOAuthSection && !autoLoggingIn && (
            <label className="flex items-start gap-2.5 cursor-pointer group py-0.5">
              <input
                type="checkbox"
                checked={oauthAgreed}
                onChange={(e) => { setOauthAgreed(e.target.checked); if (e.target.checked) setError(""); }}
                className="auth-checkbox mt-0.5"
              />
              <span className="text-xs text-muted-foreground leading-relaxed group-hover:text-foreground transition-colors">
                我已阅读并同意{" "}
                <Link href="/terms" target="_blank" className="text-[var(--em-primary)] cursor-pointer hover:underline" onClick={(e) => e.stopPropagation()}>服务条款</Link>{" "}
                和{" "}
                <Link href="/privacy" target="_blank" className="text-[var(--em-primary)] cursor-pointer hover:underline" onClick={(e) => e.stopPropagation()}>隐私政策</Link>
              </span>
            </label>
          )}

          {/* OAuth */}
          {showOAuthSection && !autoLoggingIn && (
            <div className={`grid gap-3 ${[githubEnabled, googleEnabled, qqEnabled].filter(Boolean).length >= 2 ? "grid-cols-2" : "grid-cols-1"}`}>
              {githubEnabled && (
                <Button
                  variant="outline"
                  className="auth-oauth-btn h-11 font-normal"
                  disabled={oauthLoading !== null || !oauthAgreed}
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
                  className="auth-oauth-btn h-11 font-normal"
                  disabled={oauthLoading !== null || !oauthAgreed}
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
                  className="auth-oauth-btn h-11 font-normal"
                  disabled={oauthLoading !== null || !oauthAgreed}
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
          {!autoLoggingIn && (
          <div className="space-y-3">
            <p className="text-center text-sm text-muted-foreground">
              还没有账号？{" "}
              <Link href="/register" className="text-[var(--em-primary)] hover:underline font-medium">
                注册
              </Link>
            </p>
            <div className="flex items-center justify-center gap-3 text-xs text-muted-foreground">
              <Link href="/terms" target="_blank" className="hover:text-[var(--em-primary)] transition-colors">
                服务条款
              </Link>
              <span className="text-border">|</span>
              <Link href="/privacy" target="_blank" className="hover:text-[var(--em-primary)] transition-colors">
                隐私政策
              </Link>
            </div>
          </div>
          )}
          </div>
        </motion.div>
      </div>
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
