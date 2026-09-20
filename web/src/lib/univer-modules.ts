type UniverModules = {
  createUniver: typeof import("@univerjs/presets").createUniver;
  LocaleType: typeof import("@univerjs/presets").LocaleType;
  UniverSheetsCorePreset: typeof import("@univerjs/preset-sheets-core").UniverSheetsCorePreset;
  sheetsCoreZhCN: typeof import("@univerjs/preset-sheets-core/locales/zh-CN").default;
  mergeWorksheetSnapshotWithDefault: typeof import("@univerjs/core").mergeWorksheetSnapshotWithDefault;
};

let _univerModuleCache: Promise<UniverModules> | null = null;

export function getUniverModules() {
  if (!_univerModuleCache) {
    _univerModuleCache = Promise.all([
      import("@univerjs/presets"),
      import("@univerjs/preset-sheets-core"),
      import("@univerjs/preset-sheets-core/locales/zh-CN"),
      import("@univerjs/preset-sheets-core/lib/index.css"),
      import("@univerjs/core"),
    ]).then(([presetsMod, sheetCoreMod, zhCNMod, , coreMod]) => ({
      createUniver: presetsMod.createUniver,
      LocaleType: presetsMod.LocaleType,
      UniverSheetsCorePreset: sheetCoreMod.UniverSheetsCorePreset,
      sheetsCoreZhCN: zhCNMod.default,
      mergeWorksheetSnapshotWithDefault: coreMod.mergeWorksheetSnapshotWithDefault,
    })).catch((error) => { _univerModuleCache = null; throw error; });
  }
  return _univerModuleCache;
}

/** 立即开始拉取 Univer，供 hover / 打开表格时抢跑。 */
export function warmUniverModules() {
  return getUniverModules();
}

/**
 * 预加载 Univer 库。在应用启动后调用，让后续打开面板时无需等待模块下载。
 */
export function prefetchUniverModules() {
  if (typeof window === "undefined") return;
  const schedule =
    window.requestIdleCallback ??
    ((cb: () => void) => setTimeout(cb, 8000));
  schedule(() => {
    void getUniverModules().catch(() => {});
  }, { timeout: 8000 });
}
