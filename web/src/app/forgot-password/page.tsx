"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, AlertCircle, MailCheck, CheckCircle2, Eye, EyeOff, ArrowLeft } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { forgotPassword, resetPassword, resendCode } from "@/lib/auth-api";

const cardVariants = {
  hidden: { opacity: 0, y: 24 },
  visible: { opacity: 1, y: 0, transition: { duration: 0.4, ease: "easeOut" as const } },
};

type Step = "email" | "code" | "done";

// ── 6-digit code input ────────────────────────────────────

function CodeInput({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const digits = value.padEnd(6, " ").split("");

  useEffect(() => { inputRef.current?.focus(); }, []);

  return (
    <div className="relative">
      <input
        ref={inputRef}
        type="text"
        inputMode="numeric"
        pattern="[0-9]*"
        maxLength={6}
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 6))}
        disabled={disabled}
        className="absolute inset-0 opacity-0 w-full cursor-text"
        autoComplete="one-time-code"
      />
      <div className="flex gap-2 justify-center" onClick={() => inputRef.current?.focus()}>
        {digits.map((ch, i) => (
          <div
            key={i}
            className={`w-11 flex items-center justify-center text-xl font-bold rounded-lg border-2 transition-all select-none
              ${i === value.length
                ? "border-[var(--em-primary)] bg-[var(--em-primary)]/5 shadow-sm"
                : ch.trim() ? "border-border bg-background" : "border-border/50 bg-muted/30"
              }`}
            style={{ height: "52px" }}
          >
            {ch.trim() || ""}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [resendCooldown, setResendCooldown] = useState(0);
  const [resendLoading, setResendLoading] = useState(false);
  const cooldownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startCooldown = (seconds = 60) => {
    setResendCooldown(seconds);
    cooldownRef.current = setInterval(() => {
      setResendCooldown((s) => {
        if (s <= 1) { clearInterval(cooldownRef.current!); return 0; }
        return s - 1;
      });
    }, 1000);
  };

  useEffect(() => () => clearInterval(cooldownRef.current!), []);

  // Step 1: submit email
  const handleSendCode = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await forgotPassword(email.trim());
      setStep("code");
      startCooldown(60);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "请求失败");
    } finally {
      setLoading(false);
    }
  }, [email]);

  // Step 2: verify code + set new password
  const handleReset = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (code.length !== 6) return;
    if (newPassword.length < 8) { setError("密码至少 8 个字符"); return; }
    setError("");
    setLoading(true);
    try {
      await resetPassword(email.trim(), code, newPassword);
      setStep("done");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "重置失败");
    } finally {
      setLoading(false);
    }
  }, [email, code, newPassword]);

  // Auto-submit when 6 digits entered and password filled
  useEffect(() => {
    if (step === "code" && code.length === 6 && newPassword.length >= 8 && !loading) {
      const timer = setTimeout(() => {
        const form = document.getElementById("reset-form") as HTMLFormElement;
        form?.requestSubmit();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [code, newPassword, step, loading]);

  const handleResend = useCallback(async () => {
    if (resendCooldown > 0 || resendLoading) return;
    setResendLoading(true);
    setError("");
    try {
      await resendCode(email.trim(), "reset_password");
      startCooldown(60);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "发送失败");
    } finally {
      setResendLoading(false);
    }
  }, [email, resendCooldown, resendLoading]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-background to-muted/30 px-4">
      <AnimatePresence mode="wait">
        {/* ── Step 1: Enter email ── */}
        {step === "email" && (
          <motion.div
            key="email"
            className="w-full max-w-[400px] space-y-6"
            variants={cardVariants}
            initial="hidden"
            animate="visible"
            exit={{ opacity: 0, y: -16, transition: { duration: 0.2 } }}
          >
            <div className="text-center space-y-2">
              <img src="/logo.svg" alt="ExcelManus" className="h-12 w-12 mx-auto rounded-xl shadow-md" />
              <h1 className="text-2xl font-bold tracking-tight">找回密码</h1>
              <p className="text-muted-foreground text-sm">输入您的注册邮箱，我们将发送验证码</p>
            </div>

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

            <form onSubmit={handleSendCode} className="space-y-4">
              <div className="space-y-1.5">
                <label className="block text-sm font-medium" htmlFor="email">邮箱</label>
                <input
                  id="email"
                  type="email"
                  required
                  autoFocus
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full h-11 rounded-lg border border-border bg-background px-3 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent placeholder:text-muted-foreground/50"
                  placeholder="you@example.com"
                />
              </div>

              <Button
                type="submit"
                disabled={!email.trim() || loading}
                className="w-full h-11 text-white font-medium"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "发送验证码"}
              </Button>
            </form>

            <p className="text-center text-sm text-muted-foreground">
              <Link href="/login" className="text-[var(--em-primary)] hover:underline inline-flex items-center gap-1">
                <ArrowLeft className="h-3 w-3" />
                返回登录
              </Link>
            </p>
          </motion.div>
        )}

        {/* ── Step 2: Enter code + new password ── */}
        {step === "code" && (
          <motion.div
            key="code"
            className="w-full max-w-[400px] space-y-6"
            variants={cardVariants}
            initial="hidden"
            animate="visible"
            exit={{ opacity: 0, y: -16, transition: { duration: 0.2 } }}
          >
            <div className="text-center space-y-3">
              <div className="flex items-center justify-center w-16 h-16 mx-auto rounded-2xl bg-[var(--em-primary)]/10">
                <MailCheck className="h-8 w-8 text-[var(--em-primary)]" />
              </div>
              <h1 className="text-2xl font-bold tracking-tight">重置密码</h1>
              <p className="text-muted-foreground text-sm leading-relaxed">
                验证码已发送至<br />
                <span className="font-medium text-foreground">{email}</span>
              </p>
            </div>

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

            <form id="reset-form" onSubmit={handleReset} className="space-y-5">
              <div className="space-y-2">
                <p className="text-sm text-center text-muted-foreground">输入 6 位验证码</p>
                <CodeInput value={code} onChange={setCode} disabled={loading} />
              </div>

              <div className="space-y-1.5">
                <label className="block text-sm font-medium" htmlFor="newPassword">新密码</label>
                <div className="relative">
                  <input
                    id="newPassword"
                    type={showPassword ? "text" : "password"}
                    required
                    autoComplete="new-password"
                    minLength={8}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
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
                {newPassword.length > 0 && newPassword.length < 8 && (
                  <p className="text-xs text-destructive">密码至少 8 个字符</p>
                )}
              </div>

              <Button
                type="submit"
                disabled={code.length !== 6 || newPassword.length < 8 || loading}
                className="w-full h-11 text-white font-medium"
                style={{ backgroundColor: "var(--em-primary)" }}
              >
                {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : "重置密码"}
              </Button>
            </form>

            <div className="text-center space-y-2">
              <p className="text-sm text-muted-foreground">
                没有收到邮件？{" "}
                <button
                  type="button"
                  onClick={handleResend}
                  disabled={resendCooldown > 0 || resendLoading}
                  className={`font-medium transition-colors ${
                    resendCooldown > 0 || resendLoading
                      ? "text-muted-foreground cursor-not-allowed"
                      : "text-[var(--em-primary)] hover:underline"
                  }`}
                >
                  {resendLoading
                    ? "发送中..."
                    : resendCooldown > 0
                    ? `重新发送 (${resendCooldown}s)`
                    : "重新发送"}
                </button>
              </p>
              <button
                type="button"
                onClick={() => { setStep("email"); setCode(""); setError(""); }}
                className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              >
                ← 返回修改邮箱
              </button>
            </div>
          </motion.div>
        )}

        {/* ── Step 3: Done ── */}
        {step === "done" && (
          <motion.div
            key="done"
            className="w-full max-w-[400px] space-y-6 text-center"
            variants={cardVariants}
            initial="hidden"
            animate="visible"
          >
            <div className="space-y-3">
              <div className="flex items-center justify-center w-16 h-16 mx-auto rounded-2xl bg-green-500/10">
                <CheckCircle2 className="h-8 w-8 text-green-500" />
              </div>
              <h1 className="text-2xl font-bold tracking-tight">密码重置成功</h1>
              <p className="text-muted-foreground text-sm">
                请使用新密码登录您的账号
              </p>
            </div>

            <Button
              onClick={() => router.push("/login")}
              className="w-full h-11 text-white font-medium"
              style={{ backgroundColor: "var(--em-primary)" }}
            >
              前往登录
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
