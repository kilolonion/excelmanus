const test = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { configureWindowNavigation, initialWindowBounds } = require("../src/window-compat");

function fakeWindow() {
  const webContents = new EventEmitter();
  webContents.setWindowOpenHandler = (handler) => { webContents.open = handler; };
  return { webContents, loaded: [], async loadURL(url) { this.loaded.push(url); } };
}
function fixture() {
  const window = fakeWindow();
  const opened = [];
  configureWindowNavigation(window, {
    getFrontendUrl: () => "http://127.0.0.1:54321/",
    openExternal: async (url) => opened.push(url),
    onError: (error) => { throw error; },
    oauthPreload: "/app/oauth-preload.js",
  });
  return { window, opened };
}

test("HTTP and HTTPS links work both with and without target=_blank", async () => {
  const { window, opened } = fixture();
  for (const url of ["http://example.com/help", "https://example.com/help"]) {
    assert.equal(window.webContents.open({ url }).action, "deny");
    let blocked = false;
    window.webContents.emit("will-navigate", { preventDefault() { blocked = true; } }, url);
    assert.equal(blocked, true);
  }
  await new Promise(setImmediate);
  assert.equal(opened.length, 4);
});

test("internal links stay in the workspace and arbitrary protocols cannot launch", async () => {
  const { window, opened } = fixture();
  const url = "http://127.0.0.1:54321/auth/callback";
  window.webContents.open({ url });
  assert.deepEqual(window.loaded, [url]);
  window.webContents.emit("will-navigate", { preventDefault() { assert.fail("internal link blocked"); } }, url);
  for (const url of ["file:///C:/secret", "javascript:alert(1)", "https://user:pass@example.com", "ms-excel:ofe|u|file.xlsx"]) {
    assert.equal(window.webContents.open({ url }).action, "deny");
  }
  await new Promise(setImmediate);
  assert.deepEqual(opened, []);
});

test("OAuth opens in an isolated child, follows HTTPS identity redirects and accepts only the loopback callback", () => {
  const { window } = fixture();
  const result = window.webContents.open({ url: "https://auth.openai.com/authorize?state=test" });
  assert.equal(result.action, "allow");
  assert.deepEqual(result.overrideBrowserWindowOptions.webPreferences, {
    sandbox: true, contextIsolation: true, nodeIntegration: false, preload: "/app/oauth-preload.js",
  });
  const popup = fakeWindow();
  window.webContents.emit("did-create-window", popup);
  for (const url of ["https://accounts.google.com/login", "http://localhost:1455/auth/callback?code=test", "http://127.0.0.1:1455/auth/callback"]) {
    popup.webContents.emit("will-redirect", { preventDefault() { assert.fail(`blocked ${url}`); } }, url);
  }
  let blocked = 0;
  for (const url of ["file:///secret", "http://localhost:1455/unrelated", "http://localhost:8123/admin"]) {
    popup.webContents.emit("will-navigate", { preventDefault() { blocked += 1; } }, url);
  }
  assert.equal(blocked, 3);
});

test("window controls remain within scaled displays and displays with negative coordinates", () => {
  for (const workArea of [{ x: 0, y: 0, width: 1093, height: 570 }, { x: -1920, y: 24, width: 1920, height: 1056 }]) {
    const bounds = initialWindowBounds(workArea);
    assert.ok(bounds.x >= workArea.x && bounds.y >= workArea.y);
    assert.ok(bounds.x + bounds.width <= workArea.x + workArea.width);
    assert.ok(bounds.y + bounds.height <= workArea.y + workArea.height);
    assert.ok(bounds.minHeight <= bounds.height && bounds.minWidth <= bounds.width);
  }
});
