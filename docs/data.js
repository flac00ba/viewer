const textDecoder = new TextDecoder();

async function fetchOk(url) {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${url}`);
  }
  return response;
}

export async function loadJson(url) {
  return (await fetchOk(url)).json();
}

export async function gunzip(buffer) {
  if (typeof DecompressionStream === "undefined") {
    throw new Error("Ta przeglądarka nie obsługuje DecompressionStream (gzip).");
  }
  const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Response(stream).arrayBuffer();
}

export async function loadGzipJson(url) {
  const compressed = await (await fetchOk(url)).arrayBuffer();
  return JSON.parse(textDecoder.decode(await gunzip(compressed)));
}

function assertBytes(view, offset, count) {
  if (offset + count > view.byteLength) {
    throw new Error(`Uszkodzony chunk mapy przy bajcie ${offset}.`);
  }
}

export function parseChunk(buffer, floor, chunkX, chunkY) {
  const view = new DataView(buffer);
  if (
    view.byteLength < 8 ||
    view.getUint8(0) !== 0x59 ||
    view.getUint8(1) !== 0x4d ||
    view.getUint8(2) !== 0x43 ||
    view.getUint8(3) !== 0x31
  ) {
    throw new Error("Nieprawidłowy nagłówek chunka YMC1.");
  }
  const version = view.getUint8(4);
  if (version !== 1) {
    throw new Error(`Nieobsługiwana wersja chunka: ${version}.`);
  }
  const chunkSize = view.getUint8(5);
  const tileCount = view.getUint16(6, true);
  let offset = 8;
  const tiles = [];
  const byLocalPosition = new Map();

  for (let tileIndex = 0; tileIndex < tileCount; tileIndex += 1) {
    assertBytes(view, offset, 12);
    const localX = view.getUint8(offset);
    const localY = view.getUint8(offset + 1);
    const flags = view.getUint32(offset + 2, true);
    const houseId = view.getUint32(offset + 6, true);
    const entryCount = view.getUint16(offset + 10, true);
    offset += 12;
    const entries = [];
    for (let entryIndex = 0; entryIndex < entryCount; entryIndex += 1) {
      assertBytes(view, offset, 5);
      entries.push({
        kind: view.getUint8(offset),
        id: view.getUint16(offset + 1, true),
        value: view.getUint16(offset + 3, true),
      });
      offset += 5;
    }
    const tile = {
      x: chunkX * chunkSize + localX,
      y: chunkY * chunkSize + localY,
      z: floor,
      flags,
      houseId,
      entries,
    };
    tiles.push(tile);
    byLocalPosition.set(`${localX},${localY}`, tile);
  }
  if (offset !== view.byteLength) {
    throw new Error(`Chunk zawiera ${view.byteLength - offset} nadmiarowych bajtów.`);
  }
  return { floor, chunkX, chunkY, chunkSize, tiles, byLocalPosition };
}

function chunkKey(floor, chunkX, chunkY) {
  return `${floor}/${chunkX}/${chunkY}`;
}

function floorChunkSet(manifest, floor) {
  const descriptor = manifest.map.floors[String(floor)];
  return new Set((descriptor?.chunks ?? []).map(([x, y]) => `${x},${y}`));
}

export class ChunkStore {
  constructor(manifest, assetRoot, onChange = () => {}) {
    this.manifest = manifest;
    this.assetRoot = assetRoot;
    this.onChange = onChange;
    this.chunkSize = manifest.map.chunkSize;
    this.cache = new Map();
    this.available = new Map();
    this.clock = 0;
    this.maxChunks = 96;
  }

  hasChunk(floor, chunkX, chunkY) {
    if (!this.available.has(floor)) {
      this.available.set(floor, floorChunkSet(this.manifest, floor));
    }
    return this.available.get(floor).has(`${chunkX},${chunkY}`);
  }

  async ensure(floor, chunkX, chunkY) {
    if (!this.hasChunk(floor, chunkX, chunkY)) {
      return null;
    }
    const key = chunkKey(floor, chunkX, chunkY);
    const cached = this.cache.get(key);
    if (cached) {
      cached.used = ++this.clock;
      return cached.promise;
    }
    const record = { used: ++this.clock, value: null, promise: null };
    const url = new URL(`chunks/${floor}/${chunkX}_${chunkY}.bin.gz`, this.assetRoot);
    record.promise = fetchOk(url)
      .then((response) => response.arrayBuffer())
      .then(gunzip)
      .then((buffer) => {
        record.value = parseChunk(buffer, floor, chunkX, chunkY);
        this.onChange();
        this.evict();
        return record.value;
      })
      .catch((error) => {
        this.cache.delete(key);
        throw error;
      });
    this.cache.set(key, record);
    return record.promise;
  }

  requestVisible(floor, bounds, marginChunks = 1) {
    const minX = Math.floor(bounds.left / this.chunkSize) - marginChunks;
    const maxX = Math.floor(bounds.right / this.chunkSize) + marginChunks;
    const minY = Math.floor(bounds.top / this.chunkSize) - marginChunks;
    const maxY = Math.floor(bounds.bottom / this.chunkSize) + marginChunks;
    for (let chunkY = minY; chunkY <= maxY; chunkY += 1) {
      for (let chunkX = minX; chunkX <= maxX; chunkX += 1) {
        this.ensure(floor, chunkX, chunkY).catch(console.error);
      }
    }
  }

  loadedVisible(floor, bounds, marginChunks = 1) {
    const minX = Math.floor(bounds.left / this.chunkSize) - marginChunks;
    const maxX = Math.floor(bounds.right / this.chunkSize) + marginChunks;
    const minY = Math.floor(bounds.top / this.chunkSize) - marginChunks;
    const maxY = Math.floor(bounds.bottom / this.chunkSize) + marginChunks;
    const result = [];
    for (let chunkY = minY; chunkY <= maxY; chunkY += 1) {
      for (let chunkX = minX; chunkX <= maxX; chunkX += 1) {
        const record = this.cache.get(chunkKey(floor, chunkX, chunkY));
        if (record?.value) {
          record.used = ++this.clock;
          result.push(record.value);
        }
      }
    }
    return result;
  }

  async tileAt(x, y, floor) {
    const chunkX = Math.floor(x / this.chunkSize);
    const chunkY = Math.floor(y / this.chunkSize);
    const chunk = await this.ensure(floor, chunkX, chunkY);
    if (!chunk) {
      return null;
    }
    const localX = x - chunkX * this.chunkSize;
    const localY = y - chunkY * this.chunkSize;
    return chunk.byLocalPosition.get(`${localX},${localY}`) ?? null;
  }

  evict() {
    if (this.cache.size <= this.maxChunks) {
      return;
    }
    const candidates = [...this.cache.entries()]
      .filter(([, record]) => record.value)
      .sort((a, b) => a[1].used - b[1].used);
    while (this.cache.size > this.maxChunks && candidates.length) {
      this.cache.delete(candidates.shift()[0]);
    }
  }

  get loadedCount() {
    let count = 0;
    for (const record of this.cache.values()) {
      if (record.value) count += 1;
    }
    return count;
  }
}

export class OverviewStore {
  constructor(manifest, assetRoot, onChange = () => {}) {
    this.manifest = manifest;
    this.assetRoot = assetRoot;
    this.onChange = onChange;
    this.chunkSize = manifest.map.chunkSize;
    this.available = new Map();
    this.cache = new Map();
    this.clock = 0;
    this.maxImages = 128;
  }

  hasChunk(floor, x, y) {
    if (!this.available.has(floor)) {
      this.available.set(floor, floorChunkSet(this.manifest, floor));
    }
    return this.available.get(floor).has(`${x},${y}`);
  }

  ensure(floor, x, y) {
    if (!this.hasChunk(floor, x, y)) return Promise.resolve(null);
    const key = chunkKey(floor, x, y);
    const cached = this.cache.get(key);
    if (cached) {
      cached.used = ++this.clock;
      return cached.promise;
    }
    const record = { value: null, used: ++this.clock, promise: null };
    const url = new URL(`overview/${floor}/${x}_${y}.webp`, this.assetRoot);
    record.promise = fetchOk(url)
      .then((response) => response.blob())
      .then((blob) => createImageBitmap(blob))
      .then((image) => {
        record.value = image;
        this.onChange();
        this.evict();
        return image;
      })
      .catch((error) => {
        this.cache.delete(key);
        throw error;
      });
    this.cache.set(key, record);
    return record.promise;
  }

  requestVisible(floor, bounds) {
    const minX = Math.floor(bounds.left / this.chunkSize) - 1;
    const maxX = Math.floor(bounds.right / this.chunkSize) + 1;
    const minY = Math.floor(bounds.top / this.chunkSize) - 1;
    const maxY = Math.floor(bounds.bottom / this.chunkSize) + 1;
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) {
        this.ensure(floor, x, y).catch(console.error);
      }
    }
  }

  loadedVisible(floor, bounds) {
    const minX = Math.floor(bounds.left / this.chunkSize) - 1;
    const maxX = Math.floor(bounds.right / this.chunkSize) + 1;
    const minY = Math.floor(bounds.top / this.chunkSize) - 1;
    const maxY = Math.floor(bounds.bottom / this.chunkSize) + 1;
    const result = [];
    for (let y = minY; y <= maxY; y += 1) {
      for (let x = minX; x <= maxX; x += 1) {
        const record = this.cache.get(chunkKey(floor, x, y));
        if (record?.value) {
          record.used = ++this.clock;
          result.push({ x, y, image: record.value });
        }
      }
    }
    return result;
  }

  evict() {
    if (this.cache.size <= this.maxImages) return;
    const candidates = [...this.cache.entries()]
      .filter(([, record]) => record.value)
      .sort((a, b) => a[1].used - b[1].used);
    while (this.cache.size > this.maxImages && candidates.length) {
      const [key, record] = candidates.shift();
      record.value.close?.();
      this.cache.delete(key);
    }
  }
}
