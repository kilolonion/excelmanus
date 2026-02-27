"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  ArrowLeft,
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
import { proxyAvatarUrl } from "@/lib/api";
import {
  updateProfile,
  changePassword,
  changeEmail,
  uploadAvatar,
  fetchMyWorkspaceUsage,
  type WorkspaceUsage,
} from "@/lib/auth-api";

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

  // Append token to avatar URL for <img> tag auth
  const proxied = (() => {
    const base = proxyAvatarUrl(src);
    if (!base || !accessToken) return base;
    // Only append token for local avatar-file endpoint
    if (base.includes("/avatar-file")) {
      const sep = base.includes("?") ? "&" : "?";
      return `${base}${sep}token=${accessToken}`;
    }
    return base;
  })();

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
  const router = useRouter();
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
    router.push("/login");
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
    <div className="h-full overflow-y-auto">
      {toast && (
        <Toast
          message={toast.message}
          type={toast.type}
          onClose={() => setToast(null)}
        />
      )}

      <div className="max-w-2xl mx-auto px-4 py-8 space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.push("/")}
            className="h-8 w-8"
          >
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <h1 className="text-xl font-bold">个人中心</h1>
        </div>

        {/* Profile Header Card */}
        <motion.div
          variants={cardVariants}
          custom={0}
          initial="hidden"
          animate="visible"
          className="rounded-xl border border-border bg-card p-6 shadow-sm"
        >
          <div className="flex items-center gap-5">
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

        {/* Account Info */}
        <SectionCard title="账户信息" icon={Shield} index={4}>
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

        <div className="h-8" />
      </div>
    </div>
  );
}
