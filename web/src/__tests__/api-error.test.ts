import { describe, expect, it } from "vitest";
import { formatApiErrorMessage } from "@/lib/api-error";

describe("formatApiErrorMessage", () => {
  it("uses the string error field from our API", () => {
    expect(formatApiErrorMessage({ error: "模型名称已存在: model-2" }, 409)).toBe("模型名称已存在: model-2");
  });

  it("flattens FastAPI 422 detail objects instead of [object Object]", () => {
    const message = formatApiErrorMessage({
      detail: [{
        type: "extra_forbidden",
        loc: ["body", "clone_from"],
        msg: "Extra inputs are not permitted",
        input: "acme-demo",
      }],
    }, 422);
    expect(message).toBe("clone_from: Extra inputs are not permitted");
    expect(message).not.toContain("[object Object]");
  });

  it("joins multiple validation items", () => {
    expect(formatApiErrorMessage({
      detail: [
        { loc: ["body", "name"], msg: "Field required" },
        { loc: ["body", "model"], msg: "Field required" },
      ],
    }, 422)).toBe("name: Field required; model: Field required");
  });
});
