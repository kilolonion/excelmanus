// Pair with tests/dispatch_browser_server.py; the model uses explicit test gates.
import { createServer } from "vite";
import { fileURLToPath } from "node:url";
import path from "node:path";
const root = fileURLToPath(new URL("../", import.meta.url));
const server = await createServer({ root, configFile: false,
  resolve: { alias: [
    { find: "@/lib/univer-modules", replacement: path.join(root, "src/__tests__/fixtures/multi-workbook-modules-browser.ts") },
    { find: "@", replacement: path.join(root, "src") },
  ] }, esbuild: { jsx: "automatic" }, define: { "process.env.NODE_ENV": JSON.stringify("development") },
  server: { host: "127.0.0.1", port: 5186, strictPort: true, proxy: { "/api/v1": "http://127.0.0.1:8325" } },
});
await server.listen();
console.log("http://127.0.0.1:5186/src/__tests__/fixtures/dispatch-browser.html");
