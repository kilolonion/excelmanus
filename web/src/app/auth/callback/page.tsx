"use client";

import { useEffect, useState, useRef, useCallback, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, AlertCircle, CheckCircle2, Link2, ShieldCheck, Github } from "lucide-react";
import {
  handleOAuthCallback,
  confirmAccountMerge,
  fetchCurrentUser,
  type MergeRequiredInfo,
} from "@/lib/auth-api";
import { useAuthStore } from "@/stores/auth-store";
import { Button } from "@/components/ui/button";

type CallbackState = "processing" | "success" | "error" | "merge_confirm";

const PROVIDER_LABELS: Record<string, string> = {
  github: "GitHub",
  google: "Google",
  qq: "QQ",
};

function ProviderIcon({ provider, className }: { provider: string; className?: string }) {
  if (provider === "github") return <Github className={className} />;
  if (provider === "google")
    return (
      <svg className={className} viewBox="0 0 24 24" fill="currentColor">
        <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" />
        <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" />
        <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" />
        <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" />
      </svg>
    );
  return <Link2 className={className} />;
}

function CallbackHandler() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [state, setState] = useState<CallbackState>("processing");
  const [error, setError] = useState("");
  const [mergeInfo, setMergeInfo] = useState<MergeRequiredInfo | null>(null);
  const [merging, setMerging] = useState(false);
  const processed = useRef(false);

  useEffect(() => {
    if (processed.current) return;
    processed.current = true;

    const accessToken = searchParams.get("access_token");
    const refreshToken = searchParams.get("refresh_token");
    const errorParam = searchParams.get("error") || searchParams.get("error_description");

    // 从重定向 URL 参数检测合并场景
    if (searchParams.get("merge_required") === "1") {
      const info: MergeRequiredInfo = {
        merge_required: true,
        merge_token: searchParams.get("merge_token") || "",
        existing_email: searchParams.get("existing_email") || "",
        existing_display_name: searchParams.get("existing_display_name") || "",
        existing_has_password: searchParams.get("existing_has_password") === "1",
        existing_providers: (searchParams.get("existing_providers") || "").split(",").filter(Boolean),
        new_provider: searchParams.get("new_provider") || "",
        new_provider_display_name: searchParams.get("new_provider_display_name") || "",
        new_provider_avatar_url: null,
      };
      setMergeInfo(info);
      setState("merge_confirm");
      return;
    }

    if (errorParam) {
      setError(errorParam);
      setState("error");
      return;
    }

    if (accessToken && refreshToken) {
      const { setTokens } = useAuthStore.getState();
      setTokens(accessToken, refreshToken);
      fetchCurrentUser()
        .then(() => {
          setState("success");
          setTimeout(() => router.replace("/"), 600);
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : "用户信息获取失败，请重新登录");
          setState("error");
        });
      return;
    }

    const code = searchParams.get("code");
    if (!code) {
      setError("缺少授权码");
      setState("error");
      return;
    }

    const stateVal = searchParams.get("state") || "";
    const providerHint = searchParams.get("provider") || stateVal.split(":")[0];
    let provider: "github" | "google" | "qq" = "github";
    if (providerHint === "google") provider = "google";
    else if (providerHint === "qq") provider = "qq";

    handleOAuthCallback(provider, code, searchParams.get("state") || undefined)
      .then((result) => {
        if ("merge_required" in result && result.merge_required) {
          setMergeInfo(result);
          setState("merge_confirm");
        } else {
          setState("success");
          setTimeout(() => router.replace("/"), 600);
        }
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "OAuth 认证失败");
        setState("error");
      });
  }, [searchParams, router]);

  const handleConfirmMerge = useCallback(async () => {
    if (!mergeInfo) return;
    setMerging(true);
    setError("");
    try {
      await confirmAccountMerge(mergeInfo.merge_token);
      setState("success");
      setTimeout(() => router.replace("/"), 600);
    } catch (err) {
      setError(err instanceof Error ? err.message : "合并失败");
      setState("error");
    } finally {
      setMerging(false);
    }
  }, [mergeInfo, router]);

  const handleCancelMerge = useCallback(() => {
    router.replace("/login");
  }, [router]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-background to-muted/30 px-4">
      <div className="auth-bg-orb auth-bg-orb-1" />
      <div className="auth-bg-orb auth-bg-orb-2" />

      <div className="w-full max-w-sm text-center space-y-4 relative z-10">
        {state === "processing" && (
          <>
            <Loader2 className="h-10 w-10 mx-auto animate-spin text-[var(--em-primary)]" />
            <div>
              <h2 className="text-lg font-semibold">正在完成登录</h2>
              <p className="text-sm text-muted-foreground mt-1">请稍候...</p>
            </div>
          </>
        )}

        {state === "success" && (
          <>
            <CheckCircle2 className="h-10 w-10 mx-auto text-green-500" />
            <div>
              <h2 className="text-lg font-semibold">登录成功</h2>
              <p className="text-sm text-muted-foreground mt-1">正在跳转...</p>
            </div>
          </>
        )}

        {state === "merge_confirm" && mergeInfo && (
          <div className="bg-card border rounded-2xl p-6 shadow-lg text-left space-y-5">
            <div className="text-center space-y-2">
              <div className="mx-auto w-12 h-12 rounded-full bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center">
                <Link2 className="h-6 w-6 text-amber-600 dark:text-amber-400" />
              </div>
              <h2 className="text-lg font-semibold">检测到已有账号</h2>
              <p className="text-sm text-muted-foreground">
                该邮箱已关联一个账号，是否将新的登录方式绑定到该账号？
              </p>
            </div>

            <div className="bg-muted/50 rounded-xl p-4 space-y-3">
              <div className="text-xs text-muted-foreground font-medium uppercase tracking-wide">已有账号</div>
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-full bg-primary/10 flex items-center justify-center text-sm font-bold text-primary">
                  {(mergeInfo.existing_display_name || mergeInfo.existing_email)[0]?.toUpperCase() || "U"}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{mergeInfo.existing_display_name || "用户"}</div>
                  <div className="text-xs text-muted-foreground truncate">{mergeInfo.existing_email}</div>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {mergeInfo.existing_has_password && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-background border">
                    <ShieldCheck className="h-3 w-3" /> 密码
                  </span>
                )}
                {mergeInfo.existing_providers.map((p) => (
                  <span key={p} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs bg-background border">
                    <ProviderIcon provider={p} className="h-3 w-3" /> {PROVIDER_LABELS[p] || p}
                  </span>
                ))}
              </div>
            </div>

            <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
              <span>+</span>
              <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-primary/10 text-primary text-sm font-medium">
                <ProviderIcon provider={mergeInfo.new_provider} className="h-4 w-4" />
                {PROVIDER_LABELS[mergeInfo.new_provider] || mergeInfo.new_provider} 登录
              </span>
            </div>

            {error && (
              <div className="text-sm text-destructive bg-destructive/10 rounded-lg p-3">
                {error}
              </div>
            )}

            <div className="flex gap-3">
              <Button
                variant="outline"
                className="flex-1"
                onClick={handleCancelMerge}
                disabled={merging}
              >
                取消
              </Button>
              <Button
                className="flex-1"
                onClick={handleConfirmMerge}
                disabled={merging}
              >
                {merging ? (
                  <Loader2 className="h-4 w-4 animate-spin mr-1" />
                ) : (
                  <Link2 className="h-4 w-4 mr-1" />
                )}
                确认绑定
              </Button>
            </div>
          </div>
        )}

        {state === "error" && (
          <>
            <AlertCircle className="h-10 w-10 mx-auto text-destructive" />
            <div>
              <h2 className="text-lg font-semibold">登录失败</h2>
              <p className="text-sm text-destructive mt-1">{error}</p>
            </div>
            <div className="flex gap-2 justify-center pt-2">
              <Button variant="outline" onClick={() => router.push("/login")}>
                返回登录
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-background">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      }
    >
      <CallbackHandler />
    </Suspense>
  );
}
