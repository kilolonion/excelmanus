"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSubscriptionStatus, type SubscriptionStatus } from "@/lib/auth-api";
import { useUIStore } from "@/stores/ui-store";

export function useSubscriptionAccount(provider: string) {
  const version = useUIStore((s) => s.modelProfileVersion);
  const [status, updateStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusError, setStatusError] = useState(false);
  const request = useRef(0);
  const setStatus = useCallback((next: SubscriptionStatus) => {
    // A late status read must never revive credentials after disconnecting.
    request.current += 1;
    updateStatus(next);
    setLoading(false);
    setStatusError(false);
  }, []);
  const reloadStatus = useCallback(async () => {
    const id = ++request.current;
    setLoading(true);
    setStatusError(false);
    try {
      const next = await fetchSubscriptionStatus(provider);
      if (request.current === id) updateStatus(next);
    } catch {
      if (request.current === id) setStatusError(true);
    } finally {
      if (request.current === id) setLoading(false);
    }
  }, [provider]);
  useEffect(() => {
    void reloadStatus();
    return () => { request.current += 1; };
  }, [reloadStatus, version]);

  const completeLogin = useCallback(async () => {
    // Authorization has committed; failure to reload is a separate, recoverable error.
    setStatus({ provider, status: "connected" });
    await reloadStatus();
  }, [provider, setStatus, reloadStatus]);
  return { status, setStatus, loading, statusError, reloadStatus, completeLogin };
}
