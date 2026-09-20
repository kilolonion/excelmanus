// 生成桌面端启动等待页：SSR LoadingScreen + 内联关键 CSS + 内联图标，
// 输出单文件 splash.html，供 Electron 主窗口在服务就绪前直接 loadFile。
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const webRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const outFile = process.argv[2];
if (!outFile) {
  console.error("用法: node scripts/gen-splash.mjs <输出路径>");
  process.exit(1);
}

const work = mkdtempSync(join(tmpdir(), "em-splash-"));
try {
  const bundleFile = join(work, "splash-entry.cjs");
  // 全量打包（含 react-dom/server、lucide-react）；CJS 依赖含动态 require，
  // 只能输出 cjs 格式，临时文件可在任意目录独立 import。
  await build({
    entryPoints: [join(webRoot, "scripts", "splash-entry.tsx")],
    outfile: bundleFile,
    bundle: true,
    platform: "node",
    format: "cjs",
    tsconfig: join(webRoot, "tsconfig.json"),
    logLevel: "warning",
  });
  const bundled = await import(pathToFileURL(bundleFile).href);
  const { markup, css } = bundled.default ?? bundled;

  const icon = readFileSync(join(webRoot, "public", "icon.png")).toString("base64");
  const body = markup.replaceAll('"/icon.png"', `"data:image/png;base64,${icon}"`);
  if (body.includes('"/icon.png"')) throw new Error("icon.png 内联失败，splash.html 不能引用本地路径");

  const runtime = await build({
    entryPoints: [join(webRoot, "scripts", "splash-runtime.ts")],
    bundle: true,
    write: false,
    platform: "browser",
    format: "iife",
    minify: true,
    tsconfig: join(webRoot, "tsconfig.json"),
    logLevel: "warning",
  });
  const script = runtime.outputFiles[0].text;

  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>ExcelManus</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style></head><body style="margin:0;background:#fff">${body}<script>${script}</script></body></html>`;
  writeFileSync(outFile, html);
  console.log(`generated splash: ${outFile}`);
} finally {
  rmSync(work, { recursive: true, force: true });
}
