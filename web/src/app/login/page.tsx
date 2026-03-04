"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2, Github, Eye, EyeOff, AlertCircle, X, Clock, Shield, FileSpreadsheet, Sparkles, Bot, BarChart3 } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Suspense } from "react";
import { Button } from "@/components/ui/button";
import { login, getOAuthUrl, isTokenExpired } from "@/lib/auth-api";
import { resolveAvatarSrc } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useRecentAccountsStore, canAutoLogin, type RecentAccount } from "@/stores/recent-accounts-store";
import { useAuthConfigStore } from "@/stores/auth-config-store";
import { encryptCredential, decryptCredential } from "@/lib/credential-crypto";

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
  const proxied = resolveAvatarSrc(account.avatarUrl, null);
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
  const authConfigChecked = useAuthConfigStore((s) => s.checked);
  const githubEnabled = loginMethods.github_enabled;
  const googleEnabled = loginMethods.google_enabled;
  const qqEnabled = loginMethods.qq_enabled;
  const showOAuthSection = authConfigChecked && (githubEnabled || googleEnabled || qqEnabled);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<string | null>(null);
  const [showRecentList, setShowRecentList] = useState(true);
  const [rememberMe, setRememberMe] = useState(false);
  const [autoLoggingIn, setAutoLoggingIn] = useState(false);
  const autoLoginAttemptedRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);
  const requireAgreement = loginMethods.require_agreement;
  const [agreed, setAgreed] = useState(false);
  const [shakeKey, setShakeKey] = useState(0);

  useEffect(() => {
    const prefill = searchParams.get("email");
    if (prefill) {
      setEmail(prefill);
      setShowRecentList(false);
    }
  }, [searchParams]);

  // 已认证用户访问登录页 → 直接重定向到首页，避免闪烁
  // AppShell 对 /login 跳过了 AuthProvider，此处补偿该逻辑
  useEffect(() => {
    const state = useAuthStore.getState();
    if (state.isAuthenticated && state.accessToken && !isTokenExpired(state.accessToken)) {
      router.replace("/");
    }
  }, [router]);

  useEffect(() => {
    const handlePageShow = (e: PageTransitionEvent) => {
      if (e.persisted) setOauthLoading(null);
    };
    window.addEventListener("pageshow", handlePageShow);
    return () => window.removeEventListener("pageshow", handlePageShow);
  }, []);

  // 自动登录逻辑：检查最近账号是否有保存的密码且未过期
  useEffect(() => {
    // 仅尝试一次自动登录，防止清空邮箱后重触发
    if (autoLoginAttemptedRef.current) return;
    // 如果有 email 参数，不自动登录
    if (searchParams.get("email")) return;
    // 用户刚主动退出：本次页面生命周期内禁止自动登录
    if (skipAutoLoginAfterLogout) return;
    // 循环防护：如果本次浏览器会话中自动登录已成功过但又被重定向回来，
    // 说明 validateSession 持续失败，停止自动登录以避免无限循环。
    if (typeof window !== "undefined" && sessionStorage.getItem("auto-login-redirected") === "1") {
      sessionStorage.removeItem("auto-login-redirected");
      return;
    }
    // 如果已经有最近账号，尝试自动登录第一个可用的
    if (recentAccounts.length > 0 && !autoLoggingIn) {
      const account = recentAccounts.find(canAutoLogin);
      if (account && account.savedPassword) {
        autoLoginAttemptedRef.current = true;
        setAutoLoggingIn(true);
        setEmail(account.email);
        setShowRecentList(false);
        let timerFired = false;
        const timer = setTimeout(async () => {
          timerFired = true;
          if (!mountedRef.current) return;
          try {
            setError("");
            const plainPwd = await decryptCredential(account.savedPassword!);
            if (!mountedRef.current) return;
            if (!plainPwd) {
              setAutoLoggingIn(false);
              setError("保存的密码已失效，请重新输入");
              return;
            }
            await login(account.email, plainPwd);
            if (!mountedRef.current) return;
            // 标记自动登录已成功跳转，若被 validateSession 重定向回来则不再重试
            sessionStorage.setItem("auto-login-redirected", "1");
            router.push("/");
          } catch (err) {
            if (!mountedRef.current) return;
            console.error("自动登录失败:", err);
            setAutoLoggingIn(false);
            setEmail(account.email);
            setPassword("");
            setError("自动登录失败，请重新输入密码");
          }
        }, 100);
        // 安全超时：防止 login / decryptCredential 长时间无响应导致永久卡在 spinner
        const safetyTimer = setTimeout(() => {
          if (mountedRef.current) {
            setAutoLoggingIn(false);
            setError("自动登录超时，请手动登录");
          }
        }, 15000);
        return () => {
          clearTimeout(timer);
          clearTimeout(safetyTimer);
          // React Strict Mode 会先卸载再重新挂载，导致 timer 被取消但
          // autoLoginAttemptedRef 仍为 true、autoLoggingIn 仍为 true，
          // 使第二次挂载无法重新发起自动登录，界面永久停留在"正在自动登录"。
          // 仅当 timer 未实际执行时才重置状态，允许下次挂载重试。
          if (!timerFired) {
            autoLoginAttemptedRef.current = false;
            setAutoLoggingIn(false);
          }
        };
      }
    }
  }, [recentAccounts, searchParams, autoLoggingIn, router, skipAutoLoginAfterLogout]);

  const canSubmit = email.trim().length > 0 && password.length > 0 && !loading && (!requireAgreement || agreed);

  const triggerAgreementShake = useCallback(() => {
    setShakeKey((k) => k + 1);
  }, []);

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (requireAgreement && !agreed) {
      setError("请先阅读并同意《服务条款》和《隐私政策》后再登录");
      triggerAgreementShake();
      return;
    }
    setError("");
    setLoading(true);
    try {
      await login(email.trim(), password);
      // 登录成功后，记录到最近账号（如果选择了记住我，加密保存密码）
      const encPwd = rememberMe ? await encryptCredential(password) : undefined;
      recordLogin({
        email: email.trim(),
        displayName: "",
        avatarUrl: null,
        password: encPwd,
        rememberMe,
      });
      // 手动登录成功：清除循环防护标记，允许后续自动登录正常工作
      sessionStorage.removeItem("auto-login-redirected");
      router.push("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }, [email, password, router, rememberMe, recordLogin, requireAgreement, agreed, triggerAgreementShake]);

  const handleOAuth = useCallback(async (provider: "github" | "google" | "qq") => {
    if (requireAgreement && !agreed) {
      setError("请先阅读并同意《服务条款》和《隐私政策》后再登录");
      triggerAgreementShake();
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
  }, [requireAgreement, agreed, triggerAgreementShake]);

  const handlePickAccount = useCallback(async (account: RecentAccount) => {
    setError("");
    // 如果账号有保存的密码且未过期，直接自动登录
    if (canAutoLogin(account) && account.savedPassword) {
      if (requireAgreement && !agreed) {
        setError("请先阅读并同意《服务条款》和《隐私政策》后再登录");
        triggerAgreementShake();
        return;
      }
      setAutoLoggingIn(true);
      setEmail(account.email);
      setShowRecentList(false);
      try {
        const plainPwd = await decryptCredential(account.savedPassword);
        if (!plainPwd) {
          setAutoLoggingIn(false);
          setError("保存的密码已失效，请重新输入");
          setShowRecentList(false);
          setTimeout(() => document.getElementById("password")?.focus(), 50);
          return;
        }
        await login(account.email, plainPwd);
        sessionStorage.setItem("auto-login-redirected", "1");
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
  }, [router, requireAgreement, agreed, triggerAgreementShake]);

  const showRecent = showRecentList && recentAccounts.length > 0 && !email;

  return (
    <div className="h-viewport flex bg-gradient-to-b from-background to-muted/30 relative overflow-hidden">
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
      <div className="flex-1 flex items-center justify-center px-4 py-8 overflow-y-auto relative z-10 scroll-pb-24">
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
              <button
                type="button"
                onClick={() => { setAutoLoggingIn(false); setEmail(""); setShowRecentList(true); }}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors mt-1"
              >
                取消
              </button>
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

          {/* Agreement checkbox — shown alongside quick login cards */}
          {showRecent && !autoLoggingIn && requireAgreement && (
            <motion.label
              key={`agree-quick-${shakeKey}`}
              initial={false}
              animate={shakeKey > 0 ? { x: [0, -6, 5, -4, 3, -2, 1, 0] } : { x: 0 }}
              transition={{ duration: 0.5, ease: [0.36, 0.07, 0.19, 0.97] }}
              className={`flex items-start gap-2.5 cursor-pointer group py-1.5 rounded-lg px-2 -mx-2 transition-[background-color,box-shadow] duration-300 ${
                shakeKey > 0 ? "animate-agreement-highlight" : ""
              }`}
            >
              <input
                type="checkbox"
                checked={agreed}
                onChange={(e) => { setAgreed(e.target.checked); if (e.target.checked) setError(""); }}
                className="auth-checkbox mt-0.5"
              />
              <span className="text-xs text-muted-foreground leading-relaxed group-hover:text-foreground transition-colors">
                我已阅读并同意{" "}
                <Link href="/terms" target="_blank" className="text-[var(--em-primary)] cursor-pointer hover:underline" onClick={(e) => e.stopPropagation()}>服务条款</Link>{" "}
                和{" "}
                <Link href="/privacy" target="_blank" className="text-[var(--em-primary)] cursor-pointer hover:underline" onClick={(e) => e.stopPropagation()}>隐私政策</Link>
              </span>
            </motion.label>
          )}

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

              {/* Agreement checkbox — inside form, visible for email/password login */}
              {requireAgreement && (
                <motion.label
                  key={`agree-form-${shakeKey}`}
                  initial={false}
                  animate={shakeKey > 0 ? { x: [0, -6, 5, -4, 3, -2, 1, 0] } : { x: 0 }}
                  transition={{ duration: 0.5, ease: [0.36, 0.07, 0.19, 0.97] }}
                  className={`flex items-start gap-2.5 cursor-pointer group py-1.5 rounded-lg px-2 -mx-2 transition-[background-color,box-shadow] duration-300 ${
                    shakeKey > 0 ? "animate-agreement-highlight" : ""
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={agreed}
                    onChange={(e) => { setAgreed(e.target.checked); if (e.target.checked) setError(""); }}
                    className="auth-checkbox mt-0.5"
                  />
                  <span className="text-xs text-muted-foreground leading-relaxed group-hover:text-foreground transition-colors">
                    我已阅读并同意{" "}
                    <Link href="/terms" target="_blank" className="text-[var(--em-primary)] cursor-pointer hover:underline" onClick={(e) => e.stopPropagation()}>服务条款</Link>{" "}
                    和{" "}
                    <Link href="/privacy" target="_blank" className="text-[var(--em-primary)] cursor-pointer hover:underline" onClick={(e) => e.stopPropagation()}>隐私政策</Link>
                  </span>
                </motion.label>
              )}

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
                <span className="px-3 text-muted-foreground bg-background">
                  或使用第三方账号
                </span>
              </div>
            </div>
          )}

          {/* OAuth */}
          {showOAuthSection && !autoLoggingIn && (
            <div className={`grid gap-3 ${[githubEnabled, googleEnabled, qqEnabled].filter(Boolean).length >= 2 ? "grid-cols-2" : "grid-cols-1"}`}>
              {githubEnabled && (
                <Button
                  variant="outline"
                  className="auth-oauth-btn h-11 font-normal"
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
                  className="auth-oauth-btn h-11 font-normal"
                  disabled={oauthLoading !== null}
                  onClick={() => handleOAuth("google")}
                >
                  {oauthLoading === "google" ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <>
                      <svg className="h-4 w-4 mr-2" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
                        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
                        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
                        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
                      </svg>
                      Google
                    </>
                  )}
                </Button>
              )}
              {qqEnabled && (
                <Button
                  variant="outline"
                  className="auth-oauth-btn h-11 font-normal"
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
          {!autoLoggingIn && (
          <div className="space-y-3">
            <p className="text-center text-sm text-muted-foreground">
              还没有账号？{" "}
              <Link href="/register" className="text-[var(--em-primary)] hover:underline font-medium">
                注册
              </Link>
            </p>
            {!requireAgreement && (
            <div className="flex items-center justify-center gap-3 text-xs text-muted-foreground">
              <Link href="/terms" target="_blank" className="hover:text-[var(--em-primary)] transition-colors">
                服务条款
              </Link>
              <span className="text-border">|</span>
              <Link href="/privacy" target="_blank" className="hover:text-[var(--em-primary)] transition-colors">
                隐私政策
              </Link>
            </div>
            )}
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
