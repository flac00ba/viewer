import assert from "node:assert/strict";

import { OverviewStore } from "../docs/data.js";

function record(used, onClose = () => {}) {
  return {
    value: { close: onClose },
    used,
    promise: Promise.resolve(null),
  };
}

const manifest = {
  map: {
    chunkSize: 64,
    floors: {
      0: { chunks: [[0, 0]] },
      1: { chunks: [[0, 0]] },
    },
  },
};

const store = new OverviewStore(manifest, new URL("https://example.invalid/assets/"));
const requested = [];
store.ensure = async (floor, x, y) => {
  requested.push(`${floor}/${x}/${y}`);
  return null;
};

store.setVisibleRanges([
  { floor: 0, bounds: { left: 0, top: 0, right: 0, bottom: 0 } },
  { floor: 1, bounds: { left: 0, top: 0, right: 0, bottom: 0 } },
]);

assert.deepEqual([...store.visibleKeys].sort(), ["0/0/0", "1/0/0"]);
assert.deepEqual(requested.sort(), ["0/0/0", "1/0/0"]);

let closed = 0;
store.maxImages = 1;
store.cache.set("0/0/0", record(1));
store.cache.set("9/9/9", record(2, () => {
  closed += 1;
}));
store.evict();

assert.equal(store.cache.has("0/0/0"), true, "visible image must stay cached");
assert.equal(store.cache.has("9/9/9"), false, "old invisible image should be evicted");
assert.equal(closed, 1, "evicted ImageBitmap should be closed");

store.cache.set("1/0/0", record(3));
store.evict();
assert.equal(store.cache.size, 2, "all currently visible images must survive the soft cache limit");

store.releaseVisibleRanges();
assert.equal(store.visibleKeys.size, 0);
assert.equal(store.cache.size, 1, "leaving overview mode should restore the soft cache limit");

console.log("overview cache regression test passed");
