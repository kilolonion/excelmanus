import { beforeEach, describe, expect, it } from "vitest";
import { useWordStore, type WordSnapshot } from "@/stores/word-store";

function emptySnapshot(file: string): WordSnapshot {
  return {
    file,
    total_paragraphs: 1,
    returned_paragraphs: 1,
    truncated: false,
    paragraphs: [],
    tables: [],
    total_tables: 0,
    sections: 1,
    properties: {},
  };
}

function resetWordStore() {
  useWordStore.setState({
    panelOpen: false,
    panelTab: "doc",
    activeDocPath: null,
    fullViewPath: null,
    docSnapshot: null,
    refreshCounter: 0,
    recentFiles: [],
  });
}

describe("word-store recentFiles", () => {
  beforeEach(() => {
    resetWordStore();
  });

  it("normalizes backslashes and deduplicates case-insensitively", () => {
    useWordStore.getState().addRecentFile("Docs\\A.docx");
    useWordStore.getState().addRecentFile("docs/a.docx");
    useWordStore.getState().addRecentFile("docs\\a.docx");

    expect(useWordStore.getState().recentFiles).toEqual(["docs/a.docx"]);
  });

  it("keeps at most 50 recent files, newest first", () => {
    for (let i = 0; i < 60; i++) {
      useWordStore.getState().addRecentFile(`file-${i}.docx`);
    }

    const recent = useWordStore.getState().recentFiles;
    expect(recent).toHaveLength(50);
    expect(recent[0]).toBe("file-59.docx");
    expect(recent[49]).toBe("file-10.docx");
    expect(recent).not.toContain("file-9.docx");
  });
});

describe("word-store panel tabs", () => {
  beforeEach(() => {
    resetWordStore();
  });

  it("openPanel switches to the doc tab", () => {
    useWordStore.setState({ panelTab: "history" });
    useWordStore.getState().openPanel("docs/a.docx");

    const state = useWordStore.getState();
    expect(state.panelOpen).toBe(true);
    expect(state.panelTab).toBe("doc");
    expect(state.activeDocPath).toBe("docs/a.docx");
  });

  it("openHistory opens the history tab for the given document", () => {
    useWordStore.getState().openHistory("a.docx");

    const state = useWordStore.getState();
    expect(state.panelOpen).toBe(true);
    expect(state.panelTab).toBe("history");
    expect(state.activeDocPath).toBe("a.docx");
  });

  it("openHistory without a path is a no-op when no document is open", () => {
    useWordStore.getState().openHistory();

    const state = useWordStore.getState();
    expect(state.panelOpen).toBe(false);
    expect(state.panelTab).toBe("doc");
    expect(state.activeDocPath).toBeNull();
  });
});

describe("word-store handleFilesChanged", () => {
  beforeEach(() => {
    resetWordStore();
  });

  it("bumps refreshCounter and invalidates snapshot when the active doc changes", () => {
    const snapshot = emptySnapshot("docs/a.docx");
    useWordStore.setState({
      activeDocPath: "docs/a.docx",
      docSnapshot: snapshot,
      refreshCounter: 2,
      recentFiles: ["docs/a.docx"],
    });

    useWordStore.getState().handleFilesChanged(["docs\\a.docx"]);

    const state = useWordStore.getState();
    expect(state.refreshCounter).toBe(3);
    expect(state.docSnapshot).toBeNull();
  });

  it("bumps refreshCounter and invalidates snapshot when the snapshot file changes", () => {
    useWordStore.setState({
      activeDocPath: "docs/other.docx",
      docSnapshot: emptySnapshot("uploads/report.docx"),
      refreshCounter: 1,
    });

    useWordStore.getState().handleFilesChanged(["uploads/report.docx"]);

    const state = useWordStore.getState();
    expect(state.refreshCounter).toBe(2);
    expect(state.docSnapshot).toBeNull();
  });

  it("leaves refreshCounter and snapshot unchanged when the path is not tracked", () => {
    const snapshot = emptySnapshot("docs/a.docx");
    useWordStore.setState({
      activeDocPath: "docs/a.docx",
      docSnapshot: snapshot,
      refreshCounter: 4,
      recentFiles: ["docs/a.docx"],
    });

    useWordStore.getState().handleFilesChanged(["docs/b.docx"]);

    const state = useWordStore.getState();
    expect(state.refreshCounter).toBe(4);
    expect(state.docSnapshot).toEqual(snapshot);
    expect(state.recentFiles[0]).toBe("docs/b.docx");
  });

  it("does not change state for non-word files", () => {
    const snapshot = emptySnapshot("docs/a.docx");
    useWordStore.setState({
      activeDocPath: "docs/a.docx",
      docSnapshot: snapshot,
      refreshCounter: 4,
      recentFiles: ["docs/a.docx"],
    });

    useWordStore.getState().handleFilesChanged(["book.xlsx"]);

    const state = useWordStore.getState();
    expect(state.refreshCounter).toBe(4);
    expect(state.docSnapshot).toEqual(snapshot);
    expect(state.recentFiles).toEqual(["docs/a.docx"]);
  });
});
