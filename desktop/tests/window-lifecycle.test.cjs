const { test } = require("node:test");
const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { closeWindowsBeforeShutdown } = require("../src/window-lifecycle");

function windowFixture(onClose) {
  const window = new EventEmitter();
  window.webContents = new EventEmitter();
  window.isDestroyed = () => false;
  window.close = () => onClose(window);
  return window;
}

test("waits for each renderer to close before permitting service shutdown", async () => {
  const events = [];
  const windows = [1, 2].map(id => windowFixture(window => {
    events.push(`close-${id}`);
    setImmediate(() => { events.push(`closed-${id}`); window.emit("closed"); });
  }));
  assert.equal(await closeWindowsBeforeShutdown(windows), true);
  assert.deepEqual(events, ["close-1", "closed-1", "close-2", "closed-2"]);
  for (const window of windows) {
    assert.equal(window.listenerCount("closed"), 0);
    assert.equal(window.webContents.listenerCount("will-prevent-unload"), 0);
  }
});

test("pending changes cancel shutdown and leave other windows available", async () => {
  const pending = windowFixture(window => window.webContents.emit("will-prevent-unload"));
  const other = windowFixture(() => assert.fail("must not close another window after cancellation"));
  assert.equal(await closeWindowsBeforeShutdown([pending, other]), false);
  pending.close = () => pending.emit("closed");
  assert.equal(await closeWindowsBeforeShutdown([pending]), true);
});

test("native cancellation or an unresponsive renderer never permits backend teardown", async () => {
  const cancelled = windowFixture(window => window.emit("close", { defaultPrevented: true }));
  assert.equal(await closeWindowsBeforeShutdown([cancelled]), false);
  const stalled = windowFixture(() => {});
  assert.equal(await closeWindowsBeforeShutdown([stalled], 1), false);
  assert.equal(stalled.listenerCount("closed"), 0);
});
