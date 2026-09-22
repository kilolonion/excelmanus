"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { fetchSubscriptionStatus, type SubscriptionStatus } from "@/lib/auth-api";

export function useSubscriptionAccount(provider: string, onProfileCreated: () => void, loadCatalog?: () => Promise<void>) {
  const [status, setStatus] = useState<SubscriptionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusError, setStatusError] = useState(false);
  const request = useRef(0);
  const reloadStatus = useCallback(async () => {
    const id = ++request.current;
    setLoading(true); setStatusError(false);
    try {
      const next = await fetchSubscriptionStatus(provider);
      if (request.current !== id) return;
      setStatus(next);
      if (next.status === "connected") void loadCatalog?.();
    } catch {
      if (request.current === id) setStatusError(true);
    } finally {
      if (request.current === id) setLoading(false);
    }
  }, [provider, loadCatalog]);
  useEffect(() => {
    void reloadStatus();
    return () => { request.current += 1; };
  }, [reloadStatus]);

  const completeLogin = useCallback(async () => {
    // The exchange has already succeeded. A failed status read must not report failed authorization.
    setStatus({ provider, status: "connected" });
    onProfileCreated();
    await reloadStatus();
  }, [provider, onProfileCreated, reloadStatus]);
  return { status, setStatus, loading, statusError, reloadStatus, completeLogin };
}
