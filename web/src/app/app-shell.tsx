"use client";

import { usePathname } from "next/navigation";
import { ClientLayout } from "./client-layout";
import { AuthProvider } from "@/components/providers/AuthProvider";

const AUTH_BYPASS_PATHS = ["/login", "/register"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isBypass = AUTH_BYPASS_PATHS.some((p) => pathname.startsWith(p));

  const authEnabled =
    typeof window !== "undefined" &&
    window.__EXCELMANUS_AUTH_ENABLED__ === true;

  if (isBypass) {
    return <>{children}</>;
  }

  return (
    <AuthProvider authEnabled={authEnabled}>
      <ClientLayout>{children}</ClientLayout>
    </AuthProvider>
  );
}
