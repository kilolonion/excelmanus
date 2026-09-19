import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { AT_TOP_LEVEL } from "@/components/chat/chat-input-constants";
import { buildMentionPopoverItems } from "@/components/chat/ChatMentionList";
import { formatFileMention } from "@/components/chat/chat-input-insert";

const chatDir = join(dirname(fileURLToPath(import.meta.url)), "../components/chat");

const mentionData = {
  tools: ["run_code"],
  skills: [{ name: "data_basic", description: "数据技能" }],
  files: ["uploads/sales.xlsx", "reports/sales.xlsx", "docs/"],
};

describe("AT_TOP_LEVEL", () => {
  it("only exposes file and skill categories the backend can parse", () => {
    expect(AT_TOP_LEVEL.map((cat) => cat.key)).toEqual(["file", "skill"]);
    expect(AT_TOP_LEVEL.some((cat) => cat.key === "tool" || cat.label === "工具")).toBe(false);
  });
});

describe("buildMentionPopoverItems", () => {
  it("top-level menu lists file and skill, not tool", () => {
    const items = buildMentionPopoverItems("at", "", null, mentionData);
    expect(items.map((item) => item.command)).toEqual(["@file", "@skill"]);
  });

  it("file submenu writes @file: workspace-relative paths", () => {
    const items = buildMentionPopoverItems("at-sub", "", "file", mentionData);
    expect(items.map((item) => item.command)).toEqual([
      formatFileMention({ path: "uploads/sales.xlsx" }),
      formatFileMention({ path: "reports/sales.xlsx" }),
      formatFileMention({ path: "docs/" }),
    ]);
  });

  it("does not emit @tool tokens even if atCategory is leftover tool", () => {
    const items = buildMentionPopoverItems("at-sub", "", "tool", mentionData);
    expect(items).toEqual([]);
  });

  it("typed filter file hits use @file: rather than a bare @name", () => {
    const items = buildMentionPopoverItems("at", "sales", null, mentionData);
    const fileHits = items.filter((item) => item.description === "文件");
    expect(fileHits.map((item) => item.command)).toEqual([
      "@file:uploads/sales.xlsx",
      "@file:reports/sales.xlsx",
    ]);
    expect(fileHits.every((item) => item.command.startsWith("@file:"))).toBe(true);
  });

  it("typed filter skill hits use @skill:", () => {
    const items = buildMentionPopoverItems("at", "data", null, mentionData);
    const skillHits = items.filter((item) => item.description === "数据技能");
    expect(skillHits.map((item) => item.command)).toEqual(["@skill:data_basic"]);
  });
});

describe("mention write sources", () => {
  it("pending sidebar mentions and uploads write @file: tokens, not bare @name", () => {
    const mentionList = readFileSync(join(chatDir, "ChatMentionList.tsx"), "utf8");
    expect(mentionList).toContain("formatFileMention({ path })");
    expect(mentionList).not.toContain("`@file:${filename}`");

    const upload = readFileSync(join(chatDir, "use-chat-upload.ts"), "utf8");
    expect(upload).toContain("formatFileMention");
    expect(upload).not.toMatch(/`@\$\{f\.name\}`/);
    expect(upload).not.toMatch(/`@\$\{result\.filename\}`/);
  });
});
