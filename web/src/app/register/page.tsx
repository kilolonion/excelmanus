"use client";

import { useState, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, Github, Mail, Eye, EyeOff, AlertCircle, Check, X } from "lucide-react";
import { motion } from "framer-motion";
import { Button } from "@/components/ui/button";
import { register, getOAuthUrl } from "@/lib/auth-api";

const cardVariants = {
  hidden: { opacity: 0, y: 24 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: "easeOut" as const } },
};

function getPasswordStrength(pw: string): { score: number; label: string; color: string } {
  if (!pw) return { score: 0, label: "", color: "" };
  let score = 0;
  if (pw.length >= 8) score++;
  if (pw.length >= 12) score++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
  if (/\d/.test(pw)) score++;
  if (/[^a-zA-Z0-9]/.test(pw)) score++;

  if (score <= 1) return { score: 1, label: "弱", color: "bg-red-500" };
  if (score <= 2) return { score: 2, label: "较弱", color: "bg-orange-500" };
  if (score <= 3) return { score: 3, label: "中等", color: "bg-yellow-500" };
  if (score <= 4) return { score: 4, label: "强", color: "bg-green-500" };
  return { score: 5, label: "很强", color: "bg-emerald-500" };
}

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [oauthLoading, setOauthLoading] = useState<string | null>(null);
  const [agreed, setAgreed] = useState(false);

  const strength = useMemo(() => getPasswordStrength(password), [password]);
  const passwordsMatch = confirmPassword.length > 0 && password === confirmPassword;
  const passwordsMismatch = confirmPassword.length > 0 && password !== confirmPassword;

  const validEmail = useMemo(() => {
    if (!email.trim()) return null;
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
  }, [email]);

  const canSubmit =
    email.trim().length > 0 &&
    validEmail === true &&
    password.length >= 8 &&
    passwordsMatch &&
    agreed &&
    !loading;

  const handleSubmit = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError("密码至少 8 个字符");
      return;
    }
    if (password !== confirmPassword) {
      setError("两次输入的密码不一致");
      return;
    }

    setLoading(true);
    try {
      await register(email.trim(), password, displayName.trim());
      router.push("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setLoading(false);
    }
  }, [email, password, confirmPassword, displayName, router]);

  const handleOAuth = useCallback(async (provider: "github" | "google") => {
    setOauthLoading(provider);
    setError("");
    try {
      const url = await getOAuthUrl(provider);
      window.location.href = url;
    } catch {
      setError(`${provider === "github" ? "GitHub" : "Google"} 登录暂不可用`);
      setOauthLoading(null);
    }
  }, []);

  return (
    <div className="min-h-screen flex items-start sm:items-center justify-center bg-gradient-to-b from-background to-muted/30 px-4 py-8 overflow-y-auto">
      <motion.div
        className="w-full max-w-[400px] space-y-5 my-auto sm:my-0"
        variants={cardVariants}
        initial="hidden"
        animate="visible"
      >
        {/* Header */}
        <div className="text-center space-y-2">
          <img src="/logo.svg" alt="ExcelManus" className="h-12 w-12 mx-auto rounded-xl shadow-md" />
          <h1 className="text-2xl font-bold tracking-tight">注册 ExcelManus</h1>
          <p className="text-muted-foreground text-sm">创建账号，开始智能处理 Excel</p>
        </div>

        {/* Error */}
        {error && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            className="rounded-lg bg-destructive/10 border border-destructive/20 px-3 py-2.5 text-sm text-destructive flex items-start gap-2"
          >
            <AlertCircle className="h-4 w-4 mt-0.5 flex-shrink-0" />
            <span>{error}</span>
          </motion.div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-3.5">
          {/* Display name */}
          <div className="space-y-1.5">
            <label className="block text-sm font-medium" htmlFor="displayName">昵称 <span className="text-muted-foreground font-normal">(可选)</span></label>
            <input
              id="displayName"
              type="text"
              autoComplete="name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full h-11 rounded-lg border border-border bg-background px-3 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50"
              placeholder="显示名称"
            />
          </div>

          {/* Email */}
          <div className="space-y-1.5">
            <label className="block text-sm font-medium" htmlFor="email">邮箱</label>
            <div className="relative">
              <input
                id="email"
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className={`w-full h-11 rounded-lg border bg-background px-3 pr-9 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50 ${
                  validEmail === false ? "border-destructive" : "border-border"
                }`}
                placeholder="you@example.com"
              />
              {validEmail !== null && (
                <span className={`absolute right-3 top-1/2 -translate-y-1/2 ${validEmail ? "text-green-500" : "text-destructive"}`}>
                  {validEmail ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />}
                </span>
              )}
            </div>
            {validEmail === false && (
              <p className="text-xs text-destructive">请输入有效的邮箱地址</p>
            )}
          </div>

          {/* Password */}
          <div className="space-y-1.5">
            <label className="block text-sm font-medium" htmlFor="password">密码</label>
            <div className="relative">
              <input
                id="password"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="new-password"
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full h-11 rounded-lg border border-border bg-background px-3 pr-10 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50"
                placeholder="至少 8 个字符"
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
            {/* Strength bar */}
            {password.length > 0 && (
              <div className="space-y-1">
                <div className="flex gap-1 h-1">
                  {[1, 2, 3, 4, 5].map((level) => (
                    <div
                      key={level}
                      className={`flex-1 rounded-full transition-colors ${
                        level <= strength.score ? strength.color : "bg-muted"
                      }`}
                    />
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">
                  密码强度：<span className={strength.score >= 3 ? "text-green-600" : strength.score >= 2 ? "text-yellow-600" : "text-red-600"}>{strength.label}</span>
                </p>
              </div>
            )}
          </div>

          {/* Confirm password */}
          <div className="space-y-1.5">
            <label className="block text-sm font-medium" htmlFor="confirmPassword">确认密码</label>
            <div className="relative">
              <input
                id="confirmPassword"
                type={showConfirm ? "text" : "password"}
                required
                autoComplete="new-password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className={`w-full h-11 rounded-lg border bg-background px-3 pr-10 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50 ${
                  passwordsMismatch ? "border-destructive" : "border-border"
                }`}
                placeholder="再次输入密码"
              />
              <button
                type="button"
                tabIndex={-1}
                onClick={() => setShowConfirm(!showConfirm)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
              >
                {showConfirm ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
            {passwordsMismatch && (
              <p className="text-xs text-destructive flex items-center gap-1">
                <X className="h-3 w-3" /> 密码不一致
              </p>
            )}
            {passwordsMatch && (
              <p className="text-xs text-green-600 flex items-center gap-1">
                <Check className="h-3 w-3" /> 密码一致
              </p>
            )}
          </div>

          {/* Terms */}
          <label className="flex items-start gap-2 cursor-pointer group py-1">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded border-border accent-[var(--em-primary)]"
            />
            <span className="text-xs text-muted-foreground leading-relaxed group-hover:text-foreground transition-colors">
              我已阅读并同意 <span className="text-[var(--em-primary)] cursor-pointer hover:underline">服务条款</span> 和 <span className="text-[var(--em-primary)] cursor-pointer hover:underline">隐私政策</span>
            </span>
          </label>

          <Button
            type="submit"
            disabled={!canSubmit}
            className="w-full h-11 text-white font-medium transition-all"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "创建账号"}
          </Button>
        </form>

        {/* Divider */}
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

        {/* OAuth */}
        <div className="grid grid-cols-2 gap-3">
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
        </div>

        {/* Footer */}
        <p className="text-center text-sm text-muted-foreground">
          已有账号？{" "}
          <Link href="/login" className="text-[var(--em-primary)] hover:underline font-medium">
            登录
          </Link>
        </p>
      </motion.div>
    </div>
  );
}
