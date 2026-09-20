import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterAll, describe, expect, it } from "vitest";

const webRoot = resolve(__dirname, "..", "..");
const outDir = mkdtempSync(join(tmpdir(), "gen-splash-test-"));

afterAll(() => rmSync(outDir, { recursive: true, force: true }));

describe("gen-splash", () => {
  it("生成自包含的启动等待页", () => {
    const outFile = join(outDir, "splash.html");
    execFileSync(process.execPath, [join(webRoot, "scripts", "gen-splash.mjs"), outFile]);

    const html = readFileSync(outFile, "utf8");
    expect(html).toContain(".em-splash{");
    expect(html).toContain("正在准备你的工作空间");
    expect(html).toContain("data:image/png;base64,");
    expect(html).not.toContain('src="/icon.png"');
    expect(html).toContain("__emSplashSetStatus");
  });
});
