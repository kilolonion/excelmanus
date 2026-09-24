import { build } from "esbuild";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { createHash } from "node:crypto";

const root = fileURLToPath(new URL("../", import.meta.url));
const output = path.resolve(root, "../excelmanus/workbook/render_assets");
await mkdir(output, { recursive: true });
await build({ entryPoints: [path.join(root, "src/lib/workbook-preview-entry.ts")], outfile: path.join(output, "workbench.js"),
  bundle: true, minify: true, keepNames: true, format: "iife", platform: "browser", target: ["chrome120"],
  define: { "process.env.NODE_ENV": '"production"' }, tsconfig: path.join(root, "tsconfig.json"),
  loader: { ".woff2": "dataurl", ".woff": "dataurl", ".ttf": "dataurl", ".svg": "dataurl", ".png": "dataurl" }, logLevel: "warning" });
await writeFile(path.join(output, "workbench.html"), '<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="workbench.css"><style>html,body,#workbook{margin:0;width:100%;height:100%;overflow:hidden;background:white}</style><div id="workbook"></div><script src="workbench.js"></script>');
const digest = async (file) => createHash("sha256").update(await readFile(file)).digest("hex");
const sources = {};
for (const name of ["src/lib/workbook-preview-entry.ts", "src/lib/workbook-observation.ts", "package-lock.json", "scripts/build-workbook-preview.mjs"]) sources[`web/${name}`] = await digest(path.join(root, name));
const outputs = {};
for (const name of ["workbench.js", "workbench.css", "workbench.html"]) outputs[name] = await digest(path.join(output, name));
await writeFile(path.join(output, "manifest.json"), JSON.stringify({ protocol: "workbook/2", sources, outputs }, null, 2) + "\n");
console.log("Built and fingerprinted shared workbook preview assets");
