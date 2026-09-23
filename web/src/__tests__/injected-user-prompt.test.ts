import { describe, expect, it } from "vitest";
import {
  isHiddenBackendUserMessage,
  isPureInjectedUserPrompt,
  stripInjectedUserPromptBlocks,
} from "@/lib/injected-user-prompt";

const catalog = `<system-reminder>
A skill is a reusable set of task-specific instructions. The following skills are available in this session:

<available_skills>
- \`chart_basic\`: 图表与表格技能包
- \`data_basic\`: 数据读取、分析、筛选与转换
</available_skills>

This catalog contains summaries only; do not treat a summary as the skill's instructions.
If the user already invoked a skill with /name and its body is in this conversation, do not load that skill again.
</system-reminder>`;
const jevContext = `<jev_context source="jev" authority="advisory" scope="current_turn">
{"source":"jev","authority":"advisory","suggestions":{"target":"ask"}}
</jev_context>`;

describe("stripInjectedUserPromptBlocks", () => {
  it("removes a trailing skill catalog and keeps the user prompt", () => {
    expect(stripInjectedUserPromptBlocks(`帮我识别这张收据\n\n${catalog}`)).toBe(
      "帮我识别这张收据",
    );
  });

  it("returns empty when the message is only the catalog", () => {
    expect(stripInjectedUserPromptBlocks(catalog)).toBe("");
  });

  it("removes a Jev advisory block without removing surrounding user text", () => {
    expect(stripInjectedUserPromptBlocks(`继续处理\n\n${jevContext}`)).toBe("继续处理");
    expect(stripInjectedUserPromptBlocks(jevContext)).toBe("");
  });
});

describe("isPureInjectedUserPrompt", () => {
  it("detects catalog-only payloads", () => {
    expect(isPureInjectedUserPrompt(catalog)).toBe(true);
    expect(isPureInjectedUserPrompt("帮我识别这张收据")).toBe(false);
    expect(isPureInjectedUserPrompt(`帮我识别这张收据\n${catalog}`)).toBe(false);
  });
});

describe("isHiddenBackendUserMessage", () => {
  it("honors the backend hidden flag", () => {
    expect(isHiddenBackendUserMessage({ role: "user", content: "hi", _ui_hidden: true })).toBe(
      true,
    );
  });

  it("detects catalog-only content without the flag", () => {
    expect(isHiddenBackendUserMessage({ role: "user", content: catalog })).toBe(true);
  });

  it("detects Jev advisory content without the backend flag", () => {
    expect(isHiddenBackendUserMessage({ role: "user", content: jevContext })).toBe(true);
  });
});
