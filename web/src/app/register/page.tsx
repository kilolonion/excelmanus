"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Loader2, Github, Mail } from "lucide-react";
import { Button } from "@/components/ui/button";
import { register, getOAuthUrl } from "@/lib/auth-api";

export default function RegisterPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (password.length < 8) {
      setError("密码至少 8 个字符");
      return;
    }
    setLoading(true);
    try {
      await register(email, password, displayName);
      router.push("/");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setLoading(false);
    }
  };

  const handleOAuth = async (provider: "github" | "google") => {
    try {
      const url = await getOAuthUrl(provider);
      window.location.href = url;
    } catch {
      setError(`${provider} 登录暂不可用`);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center">
          <img src="/logo.svg" alt="ExcelManus" className="h-10 w-10 mx-auto mb-3" />
          <h1 className="text-2xl font-bold">注册 ExcelManus</h1>
          <p className="text-muted-foreground text-sm mt-1">创建账号开始使用</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && (
            <div className="rounded-lg bg-destructive/10 border border-destructive/20 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          <div>
            <label className="block text-sm font-medium mb-1.5" htmlFor="displayName">昵称</label>
            <input
              id="displayName"
              type="text"
              autoComplete="name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent"
              placeholder="可选"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1.5" htmlFor="email">邮箱</label>
            <input
              id="email"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent"
              placeholder="you@example.com"
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1.5" htmlFor="password">密码</label>
            <input
              id="password"
              type="password"
              required
              autoComplete="new-password"
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full rounded-lg border border-border bg-background px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent"
              placeholder="至少 8 个字符"
            />
          </div>

          <Button
            type="submit"
            disabled={loading}
            className="w-full h-11 text-white"
            style={{ backgroundColor: "var(--em-primary)" }}
          >
            {loading && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
            注册
          </Button>
        </form>

        <div className="relative">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-border" />
          </div>
          <div className="relative flex justify-center text-xs">
            <span className="bg-background px-2 text-muted-foreground">或使用第三方账号</span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Button
            variant="outline"
            className="h-11"
            onClick={() => handleOAuth("github")}
          >
            <Github className="h-4 w-4 mr-2" />
            GitHub
          </Button>
          <Button
            variant="outline"
            className="h-11"
            onClick={() => handleOAuth("google")}
          >
            <Mail className="h-4 w-4 mr-2" />
            Google
          </Button>
        </div>

        <p className="text-center text-sm text-muted-foreground">
          已有账号？{" "}
          <Link href="/login" className="text-[var(--em-primary)] hover:underline font-medium">
            登录
          </Link>
        </p>
      </div>
    </div>
  );
}
