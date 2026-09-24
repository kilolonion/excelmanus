import { describe, expect, it } from "vitest";
import { toolIcon, toolStatusIconClass } from "@/lib/tool-icons";

const KNOWN_TOOLS = [
  "observe_spreadsheet",
  "analyze_spreadsheet",
  "compare_spreadsheets",
  "apply_spreadsheet_changes",
  "trace_spreadsheet_formulas",
  "manage_spreadsheet_versions",
  "run_code",
  "run_shell",
  "write_text_file",
  "edit_text_file",
  "read_text_file",
  "list_directory",
  "copy_file",
  "rename_file",
  "delete_file",
  "offer_download",
  "read_image",
  "skill",
  "activate_skill",
  "introspect_capability",
  "sleep",
  "finish_task",
  "read_word",
  "inspect_word",
  "search_word",
  "write_word",
  "delegate",
  "delegate_to_subagent",
] as const;

describe("toolIcon", () => {
  it("maps every known tool to an icon component", () => {
    for (const name of KNOWN_TOOLS) {
      const Icon = toolIcon(name);
      expect(Icon.displayName).toBeTruthy();
      expect(typeof (Icon as unknown as { render?: unknown }).render === "function" || typeof Icon === "function").toBe(true);
    }
  });

  it("gives distinct icons to adjacent tools in a typical run", () => {
    const names = [
      "read_image",
      "activate_skill",
      "introspect_capability",
      "run_shell",
      "list_directory",
      "observe_spreadsheet",
    ];
    const labels = names.map((name) => toolIcon(name).displayName);
    expect(new Set(labels).size).toBe(names.length);
  });

  it("falls back for unknown and mcp tools", () => {
    expect(toolIcon("mcp_filesystem_read").displayName).toBe("Wrench");
    expect(toolIcon("totally_unknown_tool").displayName).toBe("Wrench");
    expect(toolIcon("custom_spreadsheet_probe").displayName).toBe("Table2");
  });
});

describe("toolStatusIconClass", () => {
  it("uses green for success, red for failure, gray blink for running", () => {
    expect(toolStatusIconClass("success")).toContain("em-primary");
    expect(toolStatusIconClass("error")).toContain("text-red-500");
    expect(toolStatusIconClass("running")).toContain("text-muted-foreground");
    expect(toolStatusIconClass("running")).toContain("animate-tool-running-pulse");
    expect(toolStatusIconClass("streaming")).toContain("animate-tool-running-pulse");
  });

  it("keeps waiting-auth distinct from running", () => {
    expect(toolStatusIconClass("pending")).toContain("text-amber-500");
    expect(toolStatusIconClass("pending")).not.toContain("animate-tool-running-pulse");
  });
});
