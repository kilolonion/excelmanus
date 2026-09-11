"use client";

import { createContext, useContext } from "react";
import type { AdminModelCtx } from "./useAdminModelSettings";

export const AdminModelContext = createContext<AdminModelCtx | null>(null);

export function useAdminModel(): AdminModelCtx {
  const ctx = useContext(AdminModelContext);
  if (!ctx) {
    throw new Error("useAdminModel must be used within AdminModelContext");
  }
  return ctx;
}
