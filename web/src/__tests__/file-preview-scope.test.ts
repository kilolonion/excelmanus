import { beforeEach, describe, expect, it } from "vitest";
import { previewTabKey, useFilePreviewStore } from "@/stores/file-preview-store";

describe("file preview workspace scope", () => {
  beforeEach(() => {
    useFilePreviewStore.setState({
      textOpen: false,
      imageOpen: false,
      textTarget: null,
      imageTarget: null,
      previewTabs: [],
    });
  });

  it("keeps same relative path distinct across workspaces", () => {
    const store = useFilePreviewStore.getState();
    store.openText("notes.txt", "notes.txt", { sessionId: "s1", workspaceId: "w1" });
    store.openText("notes.txt", "notes.txt", { sessionId: "s2", workspaceId: "w2" });

    const tabs = useFilePreviewStore.getState().previewTabs;
    expect(tabs).toHaveLength(2);
    expect(tabs.map(previewTabKey)).toEqual(["w1|notes.txt", "w2|notes.txt"]);
    expect(useFilePreviewStore.getState().textTarget).toMatchObject({
      path: "notes.txt",
      sessionId: "s2",
      workspaceId: "w2",
    });
  });

  it("clears targets and tabs when the active session changes", () => {
    const store = useFilePreviewStore.getState();
    store.openImage("shot.png", "shot.png", { sessionId: "s1", workspaceId: "w1" });
    store.openText("notes.txt", "notes.txt", { sessionId: "s1", workspaceId: "w1" });

    useFilePreviewStore.getState().clearForSessionChange();

    expect(useFilePreviewStore.getState()).toMatchObject({
      textOpen: false,
      imageOpen: false,
      textTarget: null,
      imageTarget: null,
      previewTabs: [],
    });
  });
});
