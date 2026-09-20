export type WorkspaceSurface = "chat" | "excel" | "word" | "compare";
export type WorkbookViewLayout = "embedded" | "split";

export function workspaceLayout(surface: WorkspaceSurface, layout: WorkbookViewLayout, isMobile: boolean) {
  const split = surface === "excel" && layout === "split" && !isMobile;
  return {
    split,
    chatVisible: surface === "chat" || split,
    composerVisible: surface !== "compare" && (surface !== "excel" || !isMobile),
    fullHeightSheet: surface === "compare" || (surface === "excel" && (split || isMobile)),
  };
}

export function resolveWorkspaceSurface(input: {
  wordFullViewPath: string | null;
  compareMode: boolean;
  fullViewPath: string | null;
}): WorkspaceSurface {
  if (input.wordFullViewPath) return "word";
  if (input.compareMode) return "compare";
  if (input.fullViewPath) return "excel";
  return "chat";
}

export function rememberFullViewTarget(
  current: { path: string | null; sheet: string | null; workspaceKey?: string | null },
  last: { path: string; sheet?: string; workspaceKey?: string } | null,
): { path: string; sheet?: string; workspaceKey?: string } | null {
  if (current.path) {
    return {
      path: current.path,
      sheet: current.sheet ?? undefined,
      workspaceKey: current.workspaceKey ?? undefined,
    };
  }
  if (
    current.workspaceKey
    && last?.workspaceKey
    && current.workspaceKey !== last.workspaceKey
  ) {
    return null;
  }
  return last;
}

export function workspaceKeepAliveLayerClass(active: boolean): string {
  return active
    ? "relative flex flex-col h-full min-h-0"
    : "invisible opacity-0 pointer-events-none absolute inset-0 flex flex-col min-h-0";
}
