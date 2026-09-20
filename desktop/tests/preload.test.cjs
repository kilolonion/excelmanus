const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("preload exposes only the native folder picker", async () => {
  let exposedName = "";
  let exposedApi = null;
  let invokedChannel = "";
  const scope = {
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
            invokedChannel = channel;
            return Promise.resolve("/tmp/example");
          },
        },
      };
    },
  };

  vm.runInNewContext(
    readFileSync(path.join(__dirname, "../src/preload.js"), "utf8"),
    scope,
  );

  assert.equal(exposedName, "excelManusDesktop");
  assert.deepEqual(Object.keys(exposedApi), ["selectFolder"]);
  assert.equal(await exposedApi.selectFolder(), "/tmp/example");
  assert.equal(invokedChannel, "excelmanus:select-folder");
});
