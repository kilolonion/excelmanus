"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { ClientLayout } from "./client-layout";
import { AuthProvider } from "@/components/providers/AuthProvider";
import { useAuthConfigStore } from "@/stores/auth-config-store";

const AUTH_BYPASS_PATHS = ["/login", "/register", "/auth/callback"];
const STANDALONE_PATHS = ["/admin"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isBypass = AUTH_BYPASS_PATHS.some((p) => pathname.startsWith(p));
  const { authEnabled, checked, checkAuthEnabled } = useAuthConfigStore();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    checkAuthEnabled().finally(() => setReady(true));
  }, [checkAuthEnabled]);

  if (!ready) {
    return (
      <div className="h-screen flex items-center justify-center bg-background">
        <div className="h-7 w-7 animate-spin rounded-full border-2 border-muted-foreground border-t-transparent" />
      </div>
    );
  }

  if (isBypass) {
    return <>{children}</>;
  }

  const isStandalone = STANDALONE_PATHS.some((p) => pathname.startsWith(p));

  if (isStandalone) {
    return (
      <AuthProvider authEnabled={checked && authEnabled === true}>
        {children}
      </AuthProvider>
    );
  }

  return (
    <AuthProvider authEnabled={checked && authEnabled === true}>
      <ClientLayout>{children}</ClientLayout>
    </AuthProvider>
  );
}
