const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadPreload() {
  let exposedName = "";
  let exposedApi = null;
  const invokedChannels = [];
  const listeners = new Map();
  const dispatched = [];
  const scope = {
    window: {
      dispatchEvent(event) {
        dispatched.push(event);
        return true;
      },
    },
    CustomEvent: class CustomEvent {
      constructor(type, init) {
        this.type = type;
        this.detail = init?.detail;
      }
    },
    require(name) {
      assert.equal(name, "electron");
      return {
        contextBridge: {
          exposeInMainWorld(name, api) {
            exposedName = name;
            exposedApi = api;
          },
        },
        ipcRenderer: {
          invoke(channel) {
            invokedChannels.push(channel);
            return Promise.resolve(channel === "excelmanus:select-folder" ? "/tmp/example" : { files: [], skipped: [] });
          },
          on(channel, listener) {
            listeners.set(channel, listener);
          },
          removeListener(channel, listener) {
            if (listeners.get(channel) === listener) listeners.delete(channel);
          },
        },
      };
    },
  };

  vm.runInNewContext(
    readFileSync(path.join(__dirname, "../src/preload.js"), "utf8"),
    scope,
  );

  return { exposedName, exposedApi, invokedChannels, listeners, dispatched };
}

test("preload exposes the native pickers", async () => {
  const { exposedName, exposedApi, invokedChannels } = loadPreload();

  assert.equal(exposedName, "excelManusDesktop");
  assert.deepEqual(Object.keys(exposedApi), ["selectFolder", "pickChatFiles", "mobilePairing", "checkUpdate", "downloadUpdate",
    "getUpdateStatus", "cancelUpdate", "installUpdate", "onUpdateStatus"]);
  assert.equal(await exposedApi.selectFolder(), "/tmp/example");
  assert.deepEqual(await exposedApi.pickChatFiles(), { files: [], skipped: [] });
  assert.deepEqual(invokedChannels, ["excelmanus:select-folder", "excelmanus:pick-chat-files"]);
  await exposedApi.checkUpdate();
  await exposedApi.downloadUpdate();
  assert.deepEqual(invokedChannels.slice(2), ["excelmanus:check-update", "excelmanus:download-update"]);
});

test("progress subscription strips native event and can be removed", async () => {
  const { exposedApi, listeners, invokedChannels } = loadPreload();
  const received = [];
  const unsubscribe = exposedApi.onUpdateStatus((...args) => received.push(args));
  const status = { phase: "downloading", percent: 25 };
  listeners.get("excelmanus:update-status")({ sender: "privileged" }, status);
  assert.deepEqual(received, [[status]]);
  unsubscribe();
  assert.equal(listeners.has("excelmanus:update-status"), false);
  await exposedApi.getUpdateStatus();
  await exposedApi.cancelUpdate();
  await exposedApi.installUpdate();
  assert.deepEqual(invokedChannels, ["excelmanus:update-status", "excelmanus:cancel-update", "excelmanus:install-update"]);
});

test("menu actions are forwarded to the page as DOM events", () => {
  const { listeners, dispatched } = loadPreload();

  const listener = listeners.get("excelmanus:menu-action");
  assert.equal(typeof listener, "function");

  listener({}, "new-chat");
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].type, "excelmanus:menu-action");
  assert.equal(dispatched[0].detail, "new-chat");

  listener({}, { unexpected: true });
  listener({}, null);
  assert.equal(dispatched.length, 1);
});
