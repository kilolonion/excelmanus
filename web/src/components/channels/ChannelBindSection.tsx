"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import {
  Check,
  Loader2,
  Link2,
  AlertCircle,
  Unlink,
  MessageSquare,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAuthStore } from "@/stores/auth-store";
import {
  fetchChannelLinks,
  fetchBindCodeInfo,
  confirmChannelBind,
  unlinkChannel,
  type BindCodeInfo,
  type ChannelLinkInfo,
} from "@/lib/auth-api";
import { ChannelIcon, CHANNEL_META } from "@/components/ui/ChannelIcons";

// ── OTP Digit Input ───────────────────────────────────────

function OtpInput({
  value,
  onChange,
  onComplete,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  onComplete: () => void;
  disabled?: boolean;
}) {
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);
  const digits = value.padEnd(6, " ").slice(0, 6).split("");

  const focusAt = (i: number) => {
    if (i >= 0 && i < 6) inputRefs.current[i]?.focus();
  };

  const handleChange = (i: number, char: string) => {
    if (disabled) return;
    const d = char.replace(/\D/g, "");
    if (!d) return;
    // Multi-char input (e.g. paste via onChange on some browsers): fill from position i
    if (d.length > 1) {
      const arr = [...digits];
      for (let k = 0; k < d.length && i + k < 6; k++) {
        arr[i + k] = d[k];
      }
      const next = arr.join("");
      onChange(next);
      focusAt(Math.min(i + d.length, 5));
      if (next.length === 6 && !next.includes(" ")) setTimeout(onComplete, 80);
      return;
    }
    const arr = [...digits];
    arr[i] = d[0];
    const next = arr.join("");
    onChange(next);
    if (i < 5) focusAt(i + 1);
    if (next.length === 6 && !next.includes(" ")) {
      setTimeout(onComplete, 80);
    }
  };

  const handleKeyDown = (i: number, e: React.KeyboardEvent) => {
    if (e.key === "Backspace") {
      e.preventDefault();
      const arr = [...digits];
      if (arr[i] && arr[i] !== " ") {
        arr[i] = " ";
        onChange(arr.join("").trimEnd());
      } else if (i > 0) {
        arr[i - 1] = " ";
        onChange(arr.join("").trimEnd());
        focusAt(i - 1);
      }
    } else if (e.key === "ArrowLeft") {
      focusAt(i - 1);
    } else if (e.key === "ArrowRight") {
      focusAt(i + 1);
    } else if (e.key === "Enter") {
      onComplete();
    }
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    e.preventDefault();
    const text = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6);
    if (text) {
      onChange(text);
      focusAt(Math.min(text.length, 5));
      if (text.length === 6) setTimeout(onComplete, 80);
    }
  };

  return (
    <div className="flex items-center gap-1.5 sm:gap-2">
      {digits.map((d, i) => (
        <div key={i} className="relative">
          {i === 3 && (
            <div className="absolute -left-[5px] sm:-left-[7px] top-1/2 -translate-y-1/2 w-1 sm:w-1.5 h-px bg-border" />
          )}
          <input
            ref={(el) => { inputRefs.current[i] = el; }}
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            maxLength={1}
            value={d.trim()}
            disabled={disabled}
            onChange={(e) => handleChange(i, e.target.value)}
            onKeyDown={(e) => handleKeyDown(i, e)}
            onPaste={handlePaste}
            onFocus={(e) => e.target.select()}
            className={`
              w-9 h-11 sm:w-11 sm:h-13 rounded-lg border-2 text-center text-lg sm:text-xl font-semibold font-mono
              bg-background outline-none transition-all duration-200
              ${d.trim()
                ? "border-[var(--em-primary)] shadow-[0_0_0_1px_var(--em-primary-alpha-10)]"
                : "border-border hover:border-muted-foreground/40"
              }
              focus:border-[var(--em-primary)] focus:shadow-[0_0_0_3px_var(--em-primary-alpha-10)]
              disabled:opacity-50 disabled:cursor-not-allowed
            `}
          />
        </div>
      ))}
    </div>
  );
}

// ── Channel Bind Section ──────────────────────────────────

export function ChannelBindSection({
  showToast,
}: {
  showToast: (msg: string, type: "success" | "error") => void;
}) {
  const [links, setLinks] = useState<ChannelLinkInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [bindCode, setBindCode] = useState("");
  const [binding, setBinding] = useState(false);
  const [bindPreview, setBindPreview] = useState<BindCodeInfo | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [bindError, setBindError] = useState<string | null>(null);
  const [unlinking, setUnlinking] = useState<string | null>(null);
  const [unlinkTarget, setUnlinkTarget] = useState<string | null>(null);
  const [unlinkPassword, setUnlinkPassword] = useState("");
  const hasPassword = useAuthStore((s) => s.user?.hasPassword ?? true);

  const loadLinks = useCallback(async () => {
    try {
      const data = await fetchChannelLinks();
      setLinks(data.links || []);
    } catch {
      // 绑定功能未启用或 auth 未开启时静默忽略
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadLinks();
  }, [loadLinks]);

  // Auto-preview bind code when 6 digits entered
  useEffect(() => {
    setBindError(null);
    const code = bindCode.trim();
    if (code.length === 6) {
      setPreviewLoading(true);
      fetchBindCodeInfo(code)
        .then((info) => setBindPreview(info))
        .catch(() => setBindPreview(null))
        .finally(() => setPreviewLoading(false));
    } else {
      setBindPreview(null);
    }
  }, [bindCode]);

  const handleBind = async () => {
    const code = bindCode.trim();
    if (!code || code.length < 6 || binding) return;
    setBinding(true);
    setBindError(null);
    try {
      const result = await confirmChannelBind(code);
      if (result.ok) {
        showToast(
          `已绑定 ${result.channel} 渠道`,
          "success",
        );
        setBindCode("");
        setBindPreview(null);
        loadLinks();
      } else {
        setBindError(result.error || "绑定失败");
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "绑定失败";
      setBindError(msg);
    } finally {
      setBinding(false);
    }
  };

  const openUnlinkDialog = (channel: string) => {
    setUnlinkTarget(channel);
    setUnlinkPassword("");
  };

  const closeUnlinkDialog = () => {
    setUnlinkTarget(null);
    setUnlinkPassword("");
  };

  const handleUnlinkConfirm = async () => {
    if (!unlinkTarget || unlinking) return;
    if (hasPassword && !unlinkPassword) return;
    setUnlinking(unlinkTarget);
    try {
      await unlinkChannel(unlinkTarget, hasPassword ? unlinkPassword : undefined);
      showToast(`已解绑 ${unlinkTarget}`, "success");
      setLinks((prev) => prev.filter((l) => l.channel !== unlinkTarget));
      closeUnlinkDialog();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "解绑失败";
      showToast(msg, "error");
    } finally {
      setUnlinking(null);
    }
  };

  const STEPS = [
    { num: "1", label: "获取绑定码", desc: "在 Bot 中发送 /bind" },
    { num: "2", label: "输入验证", desc: "填入 6 位数字码" },
    { num: "3", label: "完成绑定", desc: "确认后立即生效" },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center py-6 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin mr-2" />
        <span className="text-xs">加载中...</span>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <p className="text-[13px] text-muted-foreground leading-relaxed">
        将 Telegram / QQ / 飞书等 Bot 渠道绑定到当前账号，实现跨渠道的会话和工作区共享。
      </p>

      {/* ── 绑定流程指引 ── */}
      <div className="rounded-xl bg-gradient-to-br from-muted/50 to-muted/20 border border-border/60 p-3 sm:p-4">
        <div className="flex items-start">
          {STEPS.map((step, i) => (
            <div key={step.num} className="flex-1 flex items-start">
              <div className="flex flex-col items-center text-center flex-1">
                <div
                  className="h-7 w-7 sm:h-8 sm:w-8 rounded-full flex items-center justify-center text-[11px] sm:text-xs font-bold text-white shadow-sm relative"
                  style={{
                    background: `linear-gradient(135deg, var(--em-primary), var(--em-primary-dark, var(--em-primary)))`,
                  }}
                >
                  {step.num}
                  <div
                    className="absolute inset-0 rounded-full opacity-20"
                    style={{ boxShadow: "0 0 12px var(--em-primary)" }}
                  />
                </div>
                <p className="text-[10px] sm:text-[11px] font-semibold mt-1.5 sm:mt-2 leading-tight text-foreground">{step.label}</p>
                <p className="text-[9px] sm:text-[10px] text-muted-foreground mt-0.5 leading-snug px-0.5">{step.desc}</p>
              </div>
              {i < STEPS.length - 1 && (
                <div className="flex-shrink-0 w-6 sm:w-12 mt-3.5 sm:mt-4 flex items-center">
                  <div className="w-full border-t-2 border-dashed border-[var(--em-primary)]/30" />
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ── 已绑定列表 ── */}
      {links.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground mb-1">已绑定渠道</p>
          {links.map((link) => {
            const meta = CHANNEL_META[link.channel];
            return (
              <motion.div
                key={link.id}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                className="flex items-center justify-between rounded-xl border border-border bg-gradient-to-r from-background to-muted/20 px-2.5 sm:px-3.5 py-2.5 sm:py-3 shadow-sm"
              >
                <div className="flex items-center gap-3 min-w-0">
                  <div
                    className="h-8 w-8 sm:h-9 sm:w-9 rounded-xl flex items-center justify-center text-white shrink-0 shadow-sm"
                    style={{ backgroundColor: meta?.color || "#6b7280" }}
                  >
                    <ChannelIcon channel={link.channel} className="h-4 w-4 sm:h-4.5 sm:w-4.5" />
                  </div>
                  <div className="min-w-0">
                    <p className="text-sm font-semibold truncate">
                      {meta?.label || link.channel}
                      {link.display_name && (
                        <span className="text-muted-foreground ml-1.5 font-normal text-xs">
                          {link.display_name}
                        </span>
                      )}
                    </p>
                    <p className="text-[11px] text-muted-foreground truncate flex items-center gap-1">
                      <span className="h-1.5 w-1.5 rounded-full bg-green-500 shrink-0" />
                      ID: {link.platform_id} · 绑定于{" "}
                      {new Date(link.linked_at).toLocaleDateString("zh-CN")}
                    </p>
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs text-red-500 hover:text-red-600 hover:bg-red-500/10 shrink-0 rounded-lg"
                  disabled={unlinking === link.channel}
                  onClick={() => openUnlinkDialog(link.channel)}
                >
                  {unlinking === link.channel ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Unlink className="h-3 w-3 mr-1" />
                  )}
                  解绑
                </Button>
              </motion.div>
            );
          })}
        </div>
      )}

      {/* ── 解绑确认弹窗 ── */}
      <Dialog open={!!unlinkTarget} onOpenChange={(open) => { if (!open) closeUnlinkDialog(); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>确定解绑 {unlinkTarget}？</DialogTitle>
            <DialogDescription>
              此操作不可撤销，解绑后该渠道 Bot 将无法访问您的会话和工作区。
            </DialogDescription>
          </DialogHeader>
          {hasPassword && (
            <div className="space-y-2">
              <label className="text-sm font-medium">输入密码以确认</label>
              <input
                type="password"
                value={unlinkPassword}
                onChange={(e) => setUnlinkPassword(e.target.value)}
                className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm outline-none focus:ring-2 focus:ring-[var(--em-primary)] focus:border-transparent transition-shadow"
                placeholder="输入当前密码"
                onKeyDown={(e) => { if (e.key === "Enter") handleUnlinkConfirm(); }}
                autoFocus
              />
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={closeUnlinkDialog} disabled={!!unlinking}>
              取消
            </Button>
            <Button
              size="sm"
              className="bg-red-600 hover:bg-red-700 text-white"
              disabled={!!unlinking || (hasPassword && !unlinkPassword)}
              onClick={handleUnlinkConfirm}
            >
              {unlinking ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
              确认解绑
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── 绑定码输入 ── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <div
            className="h-5 w-5 rounded-md flex items-center justify-center"
            style={{ backgroundColor: "var(--em-primary-alpha-10)" }}
          >
            <MessageSquare className="h-3 w-3" style={{ color: "var(--em-primary)" }} />
          </div>
          <p className="text-sm font-semibold">输入绑定码</p>
        </div>
        <p className="text-xs text-muted-foreground leading-relaxed">
          在 Bot 中发送{" "}
          <code
            className="px-1.5 py-0.5 rounded-md font-mono font-semibold text-[11px]"
            style={{
              backgroundColor: "var(--em-primary-alpha-10)",
              color: "var(--em-primary)",
            }}
          >
            /bind
          </code>{" "}
          获取 6 位绑定码，然后在下方输入。
        </p>

        <div className="flex flex-col items-stretch gap-3">
          <OtpInput
            value={bindCode}
            onChange={setBindCode}
            onComplete={handleBind}
            disabled={binding}
          />
          <Button
            onClick={handleBind}
            disabled={bindCode.trim().length < 6 || binding}
            className="h-11 w-full sm:w-auto px-5 rounded-xl text-sm font-semibold shadow-sm transition-all duration-200 hover:shadow-md"
            style={
              bindCode.trim().length >= 6
                ? { backgroundColor: "var(--em-primary)", color: "#fff" }
                : undefined
            }
          >
            {binding ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Link2 className="h-4 w-4 mr-1.5" />
            )}
            绑定
          </Button>
        </div>

        {/* ── 绑定码预览 / 状态 ── */}
        {previewLoading && bindCode.trim().length === 6 && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-2.5 py-2.5 px-3.5 rounded-xl bg-muted/40 border border-border/50"
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
            <span className="text-xs text-muted-foreground">查询绑定码信息...</span>
          </motion.div>
        )}
        {bindPreview && !previewLoading && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-2.5 sm:gap-3 py-2.5 sm:py-3 px-3 sm:px-3.5 rounded-xl border-2 border-green-500/30 bg-green-500/5"
          >
            <div
              className="h-9 w-9 rounded-xl flex items-center justify-center text-white shrink-0 shadow-sm"
              style={{ backgroundColor: CHANNEL_META[bindPreview.channel]?.color || "#6b7280" }}
            >
              <ChannelIcon channel={bindPreview.channel} className="h-4 w-4" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">
                {CHANNEL_META[bindPreview.channel]?.label || bindPreview.channel}
              </p>
              <p className="text-[11px] text-muted-foreground truncate">
                {bindPreview.platform_display_name} (ID: {bindPreview.platform_id})
              </p>
            </div>
            <div className="h-7 w-7 rounded-full bg-green-500/15 flex items-center justify-center shrink-0">
              <Check className="h-4 w-4 text-green-600 dark:text-green-400" />
            </div>
          </motion.div>
        )}
        {!bindPreview && !previewLoading && bindCode.trim().length === 6 && !bindError && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-2.5 py-2.5 px-3.5 rounded-xl bg-red-500/8 border border-red-500/20"
          >
            <div className="h-6 w-6 rounded-full bg-red-500/15 flex items-center justify-center shrink-0">
              <AlertCircle className="h-3.5 w-3.5 text-red-500" />
            </div>
            <span className="text-xs font-medium text-red-600 dark:text-red-400">绑定码无效或已过期，请重新获取</span>
          </motion.div>
        )}
        {bindError && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-2.5 py-2.5 px-3.5 rounded-xl bg-red-500/8 border border-red-500/20"
          >
            <div className="h-6 w-6 rounded-full bg-red-500/15 flex items-center justify-center shrink-0">
              <AlertCircle className="h-3.5 w-3.5 text-red-500" />
            </div>
            <span className="text-xs font-medium text-red-600 dark:text-red-400">{bindError}</span>
          </motion.div>
        )}
      </div>
    </div>
  );
}
