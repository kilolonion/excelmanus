"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface LoginLock { acquire(key: symbol): boolean; release(key: symbol): void; }

type OAuthStart = { authorize_url: string; state: string; redirect_uri: string; mode: string };
type OAuthOptions = {
  lock?: LoginLock;
  name: string;
  messageType: string;
  start: () => Promise<OAuthStart>;
  validateUrl: (url: string) => string;
  exchange: (code: string, state: string) => Promise<unknown>;
  onConnected: () => Promise<void>;
  onError: (message: string) => void;
};

/** Keep one attempt alive across popup / manual callback recovery. */
export function useOAuthLogin(options: OAuthOptions) {
  const optionsRef = useRef(options);
  useEffect(() => { optionsRef.current = options; });
  const [phase, setPhase] = useState<"idle" | "starting" | "waiting" | "exchanging">("idle");
  const [authorizeUrl, setAuthorizeUrl] = useState("");
  const [manual, setManual] = useState(false);
  const [notice, setNotice] = useState("");
  const [pasteUrl, setPasteUrl] = useState("");
  const lease = useRef<{ lock: LoginLock; key: symbol } | null>(null);
  const attempt = useRef(0);
  const running = useRef(false);
  const session = useRef<{ state: string; redirect: URL; exchanging: boolean } | null>(null);
  const popup = useRef<Window | null>(null);
  const monitor = useRef<ReturnType<typeof setInterval> | null>(null);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  const dispose = useCallback(() => {
    if (lease.current) lease.current.lock.release(lease.current.key);
    lease.current = null;
    attempt.current += 1;
    running.current = false;
    session.current = null;
    if (monitor.current) clearInterval(monitor.current);
    if (timeout.current) clearTimeout(timeout.current);
    monitor.current = null;
    timeout.current = null;
    if (popup.current && !popup.current.closed) popup.current.close();
    popup.current = null;
  }, []);
  useEffect(() => dispose, [dispose]);

  const reset = useCallback(() => {
    dispose();
    setPhase("idle"); setAuthorizeUrl(""); setPasteUrl(""); setNotice(""); setManual(false);
  }, [dispose]);

  const exchange = useCallback(async (code: string, state: string) => {
    const current = session.current;
    if (!current || current.state !== state || current.exchanging) return;
    current.exchanging = true;
    const id = attempt.current;
    setPhase("exchanging");
    optionsRef.current.onError("");
    try {
      await optionsRef.current.exchange(code, state);
      if (attempt.current !== id) return;
      await optionsRef.current.onConnected();
      if (attempt.current === id) reset();
    } catch (error) {
      if (attempt.current !== id) return;
      reset();
      optionsRef.current.onError(error instanceof Error ? error.message : "连接失败，请重新登录");
    }
  }, [reset]);

  useEffect(() => {
    const receive = (event: MessageEvent) => {
      const current = session.current;
      if (!current || current.exchanging || event.data?.type !== optionsRef.current.messageType) return;
      if (event.origin !== current.redirect.origin && event.origin !== window.location.origin) return;
      // Errors must belong to this attempt too; a stale popup must not cancel a new login.
      if (event.data.state !== current.state) return;
      if (popup.current && event.source && event.source !== popup.current) return;
      if (event.data.error) {
        reset();
        optionsRef.current.onError(`授权未完成：${event.data.error}。请重新登录。`);
      } else if (typeof event.data.code === "string" && event.data.code) {
        void exchange(event.data.code, current.state);
      }
    };
    window.addEventListener("message", receive);
    return () => window.removeEventListener("message", receive);
  }, [exchange, reset]);

  const start = useCallback(async () => {
    if (running.current) return;
    dispose();
    const key = Symbol("oauth");
    const lock = optionsRef.current.lock;
    if (lock && !lock.acquire(key)) return;
    if (lock) lease.current = { lock, key };
    running.current = true;
    const id = attempt.current;
    setPhase("starting"); optionsRef.current.onError("");
    setPasteUrl(""); setNotice(""); setManual(false);
    // Open synchronously in the click handler so browsers retain the user gesture.
    try {
      const nativeClient = !!(window.excelManusDesktop || window.excelManusAndroid);
      const features = "width=600,height=700,toolbar=no,menubar=no";
      if (!nativeClient) popup.current = window.open("about:blank", optionsRef.current.name, features);
      const data = await optionsRef.current.start();
      if (attempt.current !== id) return;
      const url = optionsRef.current.validateUrl(data.authorize_url);
      if (!data.state) throw new Error("授权会话无效，请重试");
      session.current = { state: data.state, redirect: new URL(data.redirect_uri), exchanging: false };
      setAuthorizeUrl(url); setPhase("waiting"); setManual(data.mode === "paste");
      // Native clients dispatch full URLs themselves and may intentionally return null.
      if (nativeClient) popup.current = window.open(url, optionsRef.current.name, features);
      if (popup.current && !popup.current.closed) {
        if (!nativeClient) popup.current.location.href = url;
        monitor.current = setInterval(() => {
          if (popup.current?.closed && !session.current?.exchanging) {
            if (monitor.current) clearInterval(monitor.current);
            monitor.current = null;
            setManual(true);
            setNotice("登录窗口已关闭。可重新打开，或粘贴授权后的回调地址继续。");
          }
        }, 500);
      } else {
        setNotice(nativeClient ? "已请求打开浏览器。若未打开，请点击下方链接继续授权。" : "浏览器未打开登录窗口，请点击下方链接继续授权。");
        setManual(true);
      }
      timeout.current = setTimeout(() => {
        if (attempt.current !== id || session.current?.exchanging) return;
        reset(); optionsRef.current.onError("本次登录已超时，请重新登录。");
      }, 10 * 60 * 1000);
    } catch (error) {
      if (attempt.current !== id) return;
      reset(); optionsRef.current.onError(error instanceof Error ? error.message : "无法发起登录，请重试");
    }
  }, [dispose, reset]);

  const reopen = useCallback(() => {
    if (!session.current || session.current.exchanging || !authorizeUrl) return;
    try {
      popup.current = window.open(authorizeUrl, optionsRef.current.name, "width=600,height=700,toolbar=no,menubar=no");
      setManual(true);
      setNotice("请完成授权；若未自动返回，可粘贴完整回调地址继续。");
    } catch {
      optionsRef.current.onError("无法打开登录页，请检查浏览器弹窗设置。");
    }
  }, [authorizeUrl]);

  const submit = useCallback(async () => {
    const current = session.current;
    if (!current || current.exchanging || !pasteUrl.trim()) return;
    optionsRef.current.onError("");
    let url: URL;
    try { url = new URL(pasteUrl.trim()); }
    catch { optionsRef.current.onError("地址格式无效，请复制授权完成后地址栏中的完整地址。"); return; }
    if (url.username || url.password || url.origin !== current.redirect.origin || url.pathname !== current.redirect.pathname) {
      optionsRef.current.onError("这不是本次登录的回调地址，请复制授权完成后的完整地址。"); return;
    }
    if (url.searchParams.get("state") !== current.state) {
      optionsRef.current.onError("此地址不属于本次登录，请使用最新登录窗口中的回调地址。"); return;
    }
    if (url.searchParams.get("error")) {
      optionsRef.current.onError("授权未完成，请重新打开登录页并同意授权。"); return;
    }
    const code = url.searchParams.get("code");
    if (!code) { optionsRef.current.onError("地址缺少授权码，请在授权完成后再复制。"); return; }
    await exchange(code, current.state);
  }, [exchange, pasteUrl]);

  return { busy: phase !== "idle", phase, authorizeUrl, manual, setManual, notice, pasteUrl, setPasteUrl, start, reopen, submit, cancel: reset };
}

export interface PollLoginSession {
  state: string;
  url: string;
  code?: string;
  interval?: number;
  expires_in?: number;
}

/** Serial polling prevents overlapping exchanges; attempt IDs ignore late responses. */
export function usePollingLogin(options: {
  lock?: LoginLock;
  start: () => Promise<PollLoginSession>;
  poll: (state: string) => Promise<{ status: string }>;
  onConnected: () => Promise<void>;
  onError: (message: string) => void;
  openBrowser?: boolean;
}) {
  const optionsRef = useRef(options);
  useEffect(() => { optionsRef.current = options; });
  const [phase, setPhase] = useState<"idle" | "starting" | "waiting" | "completing">("idle");
  const [session, setSession] = useState<PollLoginSession | null>(null);
  const lease = useRef<{ lock: LoginLock; key: symbol } | null>(null);
  const attempt = useRef(0);
  const running = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const expiry = useRef<ReturnType<typeof setTimeout> | null>(null);
  const popup = useRef<Window | null>(null);
  const dispose = useCallback(() => {
    if (lease.current) lease.current.lock.release(lease.current.key);
    lease.current = null;
    attempt.current += 1; running.current = false;
    if (timer.current) clearTimeout(timer.current);
    if (expiry.current) clearTimeout(expiry.current);
    if (popup.current && !popup.current.closed) popup.current.close();
    timer.current = null; expiry.current = null; popup.current = null;
  }, []);
  useEffect(() => dispose, [dispose]);
  const cancel = useCallback(() => { dispose(); setPhase("idle"); setSession(null); }, [dispose]);
  const start = useCallback(async () => {
    if (running.current) return;
    dispose();
    const key = Symbol("polling");
    const lock = optionsRef.current.lock;
    if (lock && !lock.acquire(key)) return;
    if (lock) lease.current = { lock, key };
    running.current = true;
    const id = attempt.current;
    setPhase("starting"); setSession(null); optionsRef.current.onError("");
    try {
      const nativeClient = !!(window.excelManusDesktop || window.excelManusAndroid);
      if (optionsRef.current.openBrowser && !nativeClient) {
        popup.current = window.open("about:blank", "_blank");
        if (popup.current) popup.current.opener = null;
      }
      const data = await optionsRef.current.start();
      if (attempt.current !== id) return;
      const url = new URL(data.url);
      if (url.protocol !== "https:" || url.username || url.password) throw new Error("授权地址无效");
      if (!data.state) throw new Error("授权会话无效，请重试");
      setSession(data);
      setPhase("waiting");
      if (optionsRef.current.openBrowser && nativeClient) window.open(data.url, "_blank", "noopener,noreferrer");
      if (popup.current && !popup.current.closed) popup.current.location.href = data.url;
      expiry.current = setTimeout(() => {
        if (attempt.current !== id) return;
        cancel(); optionsRef.current.onError("登录已超时，请重新登录。");
      }, Math.max(data.expires_in ?? 600, 1) * 1000);
      const poll = async () => {
        if (attempt.current !== id) return;
        try {
          const result = await optionsRef.current.poll(data.state);
          if (attempt.current !== id) return;
          if (result.status === "connected") {
            setPhase("completing");
            if (expiry.current) clearTimeout(expiry.current);
            await optionsRef.current.onConnected();
            if (attempt.current === id) cancel();
            return;
          }
          if (result.status !== "pending") {
            throw new Error(result.status === "expired" ? "登录已过期，请重新登录。" : "授权未完成，请重新登录。");
          }
        } catch (error) {
          if (attempt.current !== id) return;
          cancel(); optionsRef.current.onError(error instanceof Error ? error.message : "无法检查授权状态，请重试");
          return;
        }
        timer.current = setTimeout(poll, Math.max(data.interval ?? 3, 3) * 1000);
      };
      timer.current = setTimeout(poll, Math.max(data.interval ?? 3, 3) * 1000);
    } catch (error) {
      if (attempt.current !== id) return;
      cancel(); optionsRef.current.onError(error instanceof Error ? error.message : "无法发起登录，请重试");
    }
  }, [cancel, dispose]);
  return { busy: phase !== "idle", phase, session, start, cancel };
}
