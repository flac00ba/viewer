import { ChunkStore, OverviewStore, loadGzipJson, loadJson } from "./data.js";
import {
  Camera,
  CreatureOverlayRenderer,
  DETAIL_ZOOM_THRESHOLD,
  DetailRenderer,
  OverviewRenderer,
} from "./renderer.js";

const ASSET_ROOT = new URL("./assets/", import.meta.url);
const MANIFEST_URL = new URL("manifest.json", ASSET_ROOT);
const TILE_PIXELS = 32;

const elements = {
  stage: document.querySelector("#map-stage"),
  overview: document.querySelector("#overview-canvas"),
  detail: document.querySelector("#detail-canvas"),
  creatureLabels: document.querySelector("#creature-label-canvas"),
  loading: document.querySelector("#loading"),
  loadingText: document.querySelector("#loading-text"),
  error: document.querySelector("#error"),
  errorText: document.querySelector("#error-text"),
  coordinates: document.querySelector("#coordinates"),
  x: document.querySelector("#coordinate-x"),
  y: document.querySelector("#coordinate-y"),
  z: document.querySelector("#coordinate-z"),
  floorUp: document.querySelector("#floor-up"),
  floorDown: document.querySelector("#floor-down"),
  resetView: document.querySelector("#reset-view"),
  share: document.querySelector("#share-view"),
  creatures: document.querySelector("#show-creatures"),
  animations: document.querySelector("#show-animations"),
  movingFrames: document.querySelector("#moving-frames"),
  creatureNames: document.querySelector("#show-creature-names"),
  multiFloor: document.querySelector("#multi-floor"),
  spawnSearchToggle: document.querySelector("#spawn-search-toggle"),
  spawnSearch: document.querySelector("#spawn-search"),
  spawnSearchClose: document.querySelector("#spawn-search-close"),
  spawnSearchInput: document.querySelector("#spawn-search-input"),
  spawnSearchBack: document.querySelector("#spawn-search-back"),
  spawnSearchSummary: document.querySelector("#spawn-search-summary"),
  spawnSearchResults: document.querySelector("#spawn-search-results"),
  mapPosition: document.querySelector("#map-position"),
  cursorPosition: document.querySelector("#cursor-position"),
  rendererStatus: document.querySelector("#renderer-status"),
  inspector: document.querySelector("#inspector"),
  inspectorTitle: document.querySelector("#inspector-title"),
  inspectorContent: document.querySelector("#inspector-content"),
  inspectorClose: document.querySelector("#inspector-close"),
  toast: document.querySelector("#toast"),
};

const options = {
  creatures: true,
  animations: true,
  movingCreatureFrames: false,
  creatureNames: true,
  multiFloor: false,
};

let manifest;
let things;
let spawnIndex = { version: 1, creatures: [] };
let spawnByCreatureId = new Map();
let camera;
let floor;
let floors = [];
let chunkStore;
let overviewStore;
let detailRenderer;
let creatureOverlayRenderer;
let overviewRenderer;
let framePending = false;
let animationTimer = 0;
let lastUrlUpdate = 0;
let activePointer = null;
let pointerMoved = false;
let cursorTile = null;
let toastTimer = 0;
let selectedSearchCreature = null;
let focusedSpawn = null;

function clampFloor(value) {
  if (!floors.length) return 7;
  const numeric = Number(value);
  if (floors.includes(numeric)) return numeric;
  return floors.reduce((best, candidate) =>
    Math.abs(candidate - numeric) < Math.abs(best - numeric) ? candidate : best
  );
}

function queryNumber(name, fallback) {
  const raw = new URLSearchParams(location.search).get(name);
  if (raw === null || raw.trim() === "") return fallback;
  const value = Number(raw);
  return Number.isFinite(value) ? value : fallback;
}

function setFloor(nextFloor) {
  const resolved = clampFloor(nextFloor);
  if (floor === resolved) return;
  floor = resolved;
  elements.z.value = String(floor);
  closeInspector();
  invalidate();
}

function changeFloor(direction) {
  const index = floors.indexOf(floor);
  const next = floors[index + direction];
  if (next !== undefined) setFloor(next);
}

function resize() {
  if (!camera || !detailRenderer || !creatureOverlayRenderer || !overviewRenderer) return;
  const width = elements.stage.clientWidth;
  const height = elements.stage.clientHeight;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  camera.resize(width, height);
  detailRenderer.resize(width, height, dpr);
  creatureOverlayRenderer.resize(width, height, dpr);
  overviewRenderer.resize(width, height, dpr);
  invalidate();
}

function visibleFloorRange() {
  if (!options.multiFloor) return [floor];
  if (floor <= 7) {
    return floors.filter((value) => value >= floor && value <= 7).sort((a, b) => b - a);
  }
  const last = Math.min(15, floor + 2);
  return floors.filter((value) => value >= floor && value <= last).sort((a, b) => b - a);
}

function sourceBounds(bounds, sourceFloor) {
  const offset = floor - sourceFloor;
  return {
    left: bounds.left + offset,
    top: bounds.top + offset,
    right: bounds.right + offset,
    bottom: bounds.bottom + offset,
  };
}

function visibleTiles(bounds, renderedFloors) {
  const result = [];
  renderedFloors.forEach((sourceFloor, floorOrder) => {
    const boundsOnSourceFloor = sourceBounds(bounds, sourceFloor);
    chunkStore.requestVisible(sourceFloor, boundsOnSourceFloor, 1);
    for (const chunk of chunkStore.loadedVisible(sourceFloor, boundsOnSourceFloor, 1)) {
      for (const tile of chunk.tiles) {
        if (
          tile.x >= boundsOnSourceFloor.left - 2 &&
          tile.x <= boundsOnSourceFloor.right + 2 &&
          tile.y >= boundsOnSourceFloor.top - 2 &&
          tile.y <= boundsOnSourceFloor.bottom + 2
        ) {
          const projection = sourceFloor - floor;
          result.push({
            ...tile,
            drawX: tile.x + projection,
            drawY: tile.y + projection,
            sourceFloor,
            floorOrder,
          });
        }
      }
    }
  });
  return result;
}

function visibleOverviews(bounds, renderedFloors) {
  const result = [];
  const ranges = renderedFloors.map((sourceFloor) => ({
    floor: sourceFloor,
    bounds: sourceBounds(bounds, sourceFloor),
  }));
  overviewStore.setVisibleRanges(ranges);

  for (const { floor: sourceFloor, bounds: boundsOnSourceFloor } of ranges) {
    for (const image of overviewStore.loadedVisible(sourceFloor, boundsOnSourceFloor)) {
      result.push({ ...image, drawOffsetTiles: sourceFloor - floor });
    }
  }
  return result;
}

function updateUrl(force = false) {
  const now = performance.now();
  if (!force && now - lastUrlUpdate < 200) return;
  lastUrlUpdate = now;
  const url = new URL(location.href);
  url.searchParams.set("x", String(camera.tileX));
  url.searchParams.set("y", String(camera.tileY));
  url.searchParams.set("z", String(floor));
  url.searchParams.set("zoom", camera.zoom.toFixed(3).replace(/0+$/, "").replace(/\.$/, ""));
  history.replaceState(null, "", url);
}

function updateHud(mode, instanceCount = 0, renderedFloorCount = 1) {
  elements.mapPosition.textContent = `X ${camera.tileX} · Y ${camera.tileY} · Z ${floor}`;
  elements.cursorPosition.textContent = cursorTile
    ? `Kursor: ${cursorTile.x}, ${cursorTile.y}`
    : "Kursor: —";
  const zoomPercent = Math.round(camera.zoom * 100);
  const floorWord = renderedFloorCount >= 2 && renderedFloorCount <= 4 ? "piętra" : "pięter";
  const floorSuffix = renderedFloorCount > 1 ? ` · ${renderedFloorCount} ${floorWord}` : "";
  elements.rendererStatus.textContent =
    mode === "detail"
      ? `${zoomPercent}% · ${chunkStore.loadedCount} chunków · ${instanceCount.toLocaleString("pl-PL")} sprite’ów${floorSuffix}`
      : `${zoomPercent}% · szybki podgląd${floorSuffix}`;
  if (!elements.coordinates.contains(document.activeElement)) {
    elements.x.value = String(camera.tileX);
    elements.y.value = String(camera.tileY);
    elements.z.value = String(floor);
  }
  elements.floorUp.disabled = floors.indexOf(floor) <= 0;
  elements.floorDown.disabled = floors.indexOf(floor) >= floors.length - 1;
}

function render(timeMs) {
  framePending = false;
  if (!camera) return;

  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const bounds = camera.visibleTileBounds();
  const renderedFloors = visibleFloorRange();
  let mode;
  let instances = 0;

  if (camera.zoom >= DETAIL_ZOOM_THRESHOLD) {
    mode = "detail";
    overviewStore.releaseVisibleRanges();
    elements.detail.hidden = false;
    elements.overview.hidden = true;
    elements.creatureLabels.hidden = false;
    const tiles = visibleTiles(bounds, renderedFloors);
    instances = detailRenderer.render(camera, tiles, timeMs, options, dpr);
    creatureOverlayRenderer.render(
      camera,
      tiles,
      things,
      options,
      dpr,
      focusedSpawn?.z === floor ? focusedSpawn : null,
      timeMs,
    );
  } else {
    mode = "overview";
    elements.detail.hidden = true;
    elements.creatureLabels.hidden = true;
    elements.overview.hidden = false;
    creatureOverlayRenderer.clear(camera, dpr);
    overviewRenderer.render(
      camera,
      visibleOverviews(bounds, renderedFloors),
      manifest.map.chunkSize,
      dpr,
    );
  }

  updateHud(mode, instances, renderedFloors.length);
  updateUrl();

  window.clearTimeout(animationTimer);
  if (mode === "detail" && options.animations) {
    animationTimer = window.setTimeout(invalidate, 40);
  }
}

function invalidate() {
  if (framePending) return;
  framePending = true;
  requestAnimationFrame(render);
}

function clientPoint(event) {
  const rect = elements.stage.getBoundingClientRect();
  return { x: event.clientX - rect.left, y: event.clientY - rect.top };
}

function comesFromInterface(event) {
  return event.target instanceof Element && event.target.closest(".panel") !== null;
}

function onPointerDown(event) {
  if (event.button !== 0 || activePointer || comesFromInterface(event)) return;
  const point = clientPoint(event);
  activePointer = { id: event.pointerId, x: point.x, y: point.y, originX: point.x, originY: point.y };
  pointerMoved = false;
  elements.stage.setPointerCapture(event.pointerId);
  elements.stage.classList.add("is-panning");
}

function onPointerMove(event) {
  const point = clientPoint(event);
  cursorTile = camera.screenToTile(point.x, point.y);
  elements.cursorPosition.textContent = `Kursor: ${cursorTile.x}, ${cursorTile.y}`;
  if (!activePointer || activePointer.id !== event.pointerId) return;
  const dx = point.x - activePointer.x;
  const dy = point.y - activePointer.y;
  if (Math.hypot(point.x - activePointer.originX, point.y - activePointer.originY) > 4) {
    pointerMoved = true;
  }
  camera.panScreen(dx, dy);
  activePointer.x = point.x;
  activePointer.y = point.y;
  invalidate();
}

function onPointerUp(event) {
  if (!activePointer || activePointer.id !== event.pointerId) return;
  const point = clientPoint(event);
  activePointer = null;
  elements.stage.classList.remove("is-panning");
  if (!pointerMoved) inspectTile(camera.screenToTile(point.x, point.y));
  updateUrl(true);
}

function onWheel(event) {
  if (comesFromInterface(event)) return;
  event.preventDefault();
  const point = clientPoint(event);
  camera.zoomAt(Math.exp(-event.deltaY * 0.0015), point.x, point.y);
  invalidate();
}

function onDoubleClick(event) {
  if (comesFromInterface(event)) return;
  const point = clientPoint(event);
  const tile = camera.screenToTile(point.x, point.y);
  camera.centerOn(tile.x, tile.y);
  invalidate();
}

function closeInspector() {
  elements.inspector.hidden = true;
}

function entryLabel(entry) {
  if (entry.kind === 1) {
    const creature = things.creatures[entry.id];
    return creature ? `Potwór: ${creature.name}` : `Potwór #${entry.id}`;
  }
  const item = things.items[String(entry.id)];
  const suffix =
    item?.flags.stackable && entry.value > 1
      ? ` × ${entry.value}`
      : item?.flags.fluid && entry.value
        ? ` (subtype ${entry.value})`
        : "";
  return item ? `${item.name} · ID ${entry.id}${suffix}` : `Item ID ${entry.id}${suffix}`;
}

async function inspectTile(position) {
  elements.inspector.hidden = false;
  elements.inspectorTitle.textContent = `${position.x}, ${position.y}, ${floor}`;
  elements.inspectorContent.innerHTML = '<p class="muted">Wczytywanie pola…</p>';
  try {
    const tile = await chunkStore.tileAt(position.x, position.y, floor);
    if (!tile) {
      elements.inspectorContent.innerHTML = '<p class="muted">Na tej pozycji nie ma zapisanego pola mapy.</p>';
      return;
    }
    const rows = tile.entries.map(
      (entry) => `<li><span class="entry-kind">${entry.kind === 1 ? "C" : "I"}</span>${escapeHtml(entryLabel(entry))}</li>`,
    );
    elements.inspectorContent.innerHTML = `
      <dl class="tile-meta">
        <div><dt>Flagi</dt><dd>0x${tile.flags.toString(16).padStart(8, "0")}</dd></div>
        ${tile.houseId ? `<div><dt>House ID</dt><dd>${tile.houseId}</dd></div>` : ""}
      </dl>
      ${rows.length ? `<ol class="thing-list">${rows.join("")}</ol>` : '<p class="muted">Puste pole.</p>'}
    `;
  } catch (error) {
    elements.inspectorContent.innerHTML = `<p class="error-copy">${escapeHtml(error.message)}</p>`;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeSearch(value) {
  return String(value)
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLocaleLowerCase("en");
}

function pluralLabel(count, singular, pluralFew, pluralMany) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (count === 1) return singular;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return pluralFew;
  return pluralMany;
}

function renderSpawnSearch() {
  if (selectedSearchCreature) {
    const positions = selectedSearchCreature.positions;
    elements.spawnSearchBack.hidden = false;
    elements.spawnSearchSummary.textContent = "Wybierz spawn, aby przejść bezpośrednio na jego pozycję.";
    elements.spawnSearchResults.innerHTML = `
      <div class="spawn-selected-card">
        <strong>${escapeHtml(selectedSearchCreature.name)}</strong>
        <span>${positions.length} ${pluralLabel(positions.length, "spawn", "spawny", "spawnów")}</span>
      </div>
      ${positions
        .map(
          ([x, y, z], index) => `
            <button class="spawn-position" type="button" data-position-index="${index}">
              <span class="spawn-position-number">${String(index + 1).padStart(2, "0")}</span>
              <strong>X ${x} · Y ${y}</strong>
              <em>Z ${z}</em>
            </button>
          `,
        )
        .join("")}
    `;
    return;
  }

  elements.spawnSearchBack.hidden = true;
  const query = normalizeSearch(elements.spawnSearchInput.value.trim());
  const matches = spawnIndex.creatures.filter((creature) => normalizeSearch(creature.name).includes(query));
  const spawnCount = matches.reduce((total, creature) => total + creature.positions.length, 0);
  elements.spawnSearchSummary.textContent = query
    ? `${matches.length} ${pluralLabel(matches.length, "wynik", "wyniki", "wyników")} · ${spawnCount} ${pluralLabel(spawnCount, "spawn", "spawny", "spawnów")}`
    : `${matches.length} rodzajów potworów · ${spawnCount} ${pluralLabel(spawnCount, "spawn", "spawny", "spawnów")}`;

  elements.spawnSearchResults.innerHTML = matches.length
    ? matches
        .map(
          (creature) => `
            <button class="spawn-result" type="button" data-creature-id="${creature.id}">
              <span class="spawn-result-name">${escapeHtml(creature.name)}</span>
              <span class="spawn-result-count">${creature.positions.length} ${pluralLabel(creature.positions.length, "spawn", "spawny", "spawnów")}</span>
            </button>
          `,
        )
        .join("")
    : '<p class="spawn-empty">Nie znaleziono takiego potwora na mapie.</p>';
}

function openSpawnSearch() {
  elements.spawnSearch.hidden = false;
  elements.spawnSearchToggle.setAttribute("aria-expanded", "true");
  renderSpawnSearch();
  window.requestAnimationFrame(() => {
    elements.spawnSearchInput.focus();
    elements.spawnSearchInput.select();
  });
}

function closeSpawnSearch() {
  elements.spawnSearch.hidden = true;
  elements.spawnSearchToggle.setAttribute("aria-expanded", "false");
}

function selectSearchCreature(creatureId) {
  selectedSearchCreature = spawnByCreatureId.get(creatureId) ?? null;
  if (!selectedSearchCreature) return;
  elements.spawnSearchInput.value = selectedSearchCreature.name;
  renderSpawnSearch();
}

function goToSpawn(positionIndex) {
  const position = selectedSearchCreature?.positions[positionIndex];
  if (!position) return;
  const [x, y, z] = position;
  options.creatures = true;
  elements.creatures.checked = true;
  focusedSpawn = {
    x,
    y,
    z,
    creatureId: selectedSearchCreature.id,
    name: selectedSearchCreature.name,
  };
  camera.centerOn(x, y);
  camera.zoom = Math.max(1, camera.zoom);
  setFloor(z);
  closeSpawnSearch();
  updateUrl(true);
  showToast(`${selectedSearchCreature.name} · ${x}, ${y}, ${z}`);
  invalidate();
}

function goToCoordinates(event) {
  event.preventDefault();
  const x = Number(elements.x.value);
  const y = Number(elements.y.value);
  const z = Number(elements.z.value);
  if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z)) return;
  camera.centerOn(Math.trunc(x), Math.trunc(y));
  setFloor(Math.trunc(z));
  updateUrl(true);
  invalidate();
}

function showToast(message) {
  elements.toast.textContent = message;
  elements.toast.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => {
    elements.toast.hidden = true;
  }, 1800);
}

async function shareView() {
  updateUrl(true);
  try {
    await navigator.clipboard.writeText(location.href);
    showToast("Link do tego widoku skopiowany.");
  } catch {
    showToast("Skopiuj adres z paska przeglądarki.");
  }
}

function onKeyDown(event) {
  const isFormControl = event.target instanceof HTMLInputElement || event.target instanceof HTMLButtonElement;
  if (event.key === "Escape" && !elements.spawnSearch.hidden) {
    closeSpawnSearch();
    event.preventDefault();
    return;
  }
  if (event.key === "/" && !isFormControl) {
    openSpawnSearch();
    event.preventDefault();
    return;
  }
  if (isFormControl) return;
  const pan = Math.max(32, Math.min(camera.width, camera.height) * 0.15);
  let handled = true;
  switch (event.key) {
    case "ArrowLeft":
      camera.panScreen(pan, 0);
      break;
    case "ArrowRight":
      camera.panScreen(-pan, 0);
      break;
    case "ArrowUp":
      camera.panScreen(0, pan);
      break;
    case "ArrowDown":
      camera.panScreen(0, -pan);
      break;
    case "+":
    case "=":
      changeFloor(-1);
      break;
    case "-":
      changeFloor(1);
      break;
    case "PageUp":
      changeFloor(-1);
      break;
    case "PageDown":
      changeFloor(1);
      break;
    case "Escape":
      closeInspector();
      break;
    default:
      handled = false;
  }
  if (handled) {
    event.preventDefault();
    invalidate();
  }
}

function bindEvents() {
  elements.stage.addEventListener("pointerdown", onPointerDown);
  elements.stage.addEventListener("pointermove", onPointerMove);
  elements.stage.addEventListener("pointerup", onPointerUp);
  elements.stage.addEventListener("pointercancel", onPointerUp);
  elements.stage.addEventListener("wheel", onWheel, { passive: false });
  elements.stage.addEventListener("dblclick", onDoubleClick);
  elements.coordinates.addEventListener("submit", goToCoordinates);
  elements.floorUp.addEventListener("click", () => changeFloor(-1));
  elements.floorDown.addEventListener("click", () => changeFloor(1));
  elements.resetView.addEventListener("click", () => {
    focusedSpawn = null;
    camera.centerOn(manifest.map.initial.x, manifest.map.initial.y);
    camera.zoom = 1;
    setFloor(manifest.map.initial.z);
    invalidate();
  });
  elements.share.addEventListener("click", shareView);
  elements.inspectorClose.addEventListener("click", closeInspector);
  elements.creatures.addEventListener("change", () => {
    options.creatures = elements.creatures.checked;
    invalidate();
  });
  elements.animations.addEventListener("change", () => {
    options.animations = elements.animations.checked;
    invalidate();
  });
  elements.movingFrames.addEventListener("change", () => {
    options.movingCreatureFrames = elements.movingFrames.checked;
    invalidate();
  });
  elements.creatureNames.addEventListener("change", () => {
    options.creatureNames = elements.creatureNames.checked;
    invalidate();
  });
  elements.multiFloor.addEventListener("change", () => {
    options.multiFloor = elements.multiFloor.checked;
    closeInspector();
    invalidate();
  });
  elements.spawnSearchToggle.addEventListener("click", () => {
    if (elements.spawnSearch.hidden) openSpawnSearch();
    else closeSpawnSearch();
  });
  elements.spawnSearchClose.addEventListener("click", closeSpawnSearch);
  elements.spawnSearchBack.addEventListener("click", () => {
    selectedSearchCreature = null;
    elements.spawnSearchInput.value = "";
    renderSpawnSearch();
    elements.spawnSearchInput.focus();
  });
  elements.spawnSearchInput.addEventListener("input", () => {
    selectedSearchCreature = null;
    renderSpawnSearch();
  });
  elements.spawnSearchResults.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const creatureButton = event.target.closest("[data-creature-id]");
    if (creatureButton) {
      selectSearchCreature(Number(creatureButton.dataset.creatureId));
      return;
    }
    const positionButton = event.target.closest("[data-position-index]");
    if (positionButton) goToSpawn(Number(positionButton.dataset.positionIndex));
  });
  window.addEventListener("keydown", onKeyDown);
  new ResizeObserver(resize).observe(elements.stage);
}

async function boot() {
  try {
    if (!("WebGL2RenderingContext" in window)) {
      throw new Error("Ta przeglądarka nie obsługuje WebGL 2.");
    }
    elements.loadingText.textContent = "Wczytuję indeks mapy…";
    manifest = await loadJson(MANIFEST_URL);
    document.title = manifest.title;
    floors = Object.keys(manifest.map.floors).map(Number).sort((a, b) => a - b);

    elements.loadingText.textContent = "Wczytuję definicje użytych sprite’ów…";
    [things, spawnIndex] = await Promise.all([
      loadGzipJson(new URL(manifest.assets.things, ASSET_ROOT)),
      manifest.assets.spawns
        ? loadGzipJson(new URL(manifest.assets.spawns, ASSET_ROOT))
        : Promise.resolve({ version: 1, creatures: [] }),
    ]);
    spawnByCreatureId = new Map(spawnIndex.creatures.map((creature) => [creature.id, creature]));

    const initial = manifest.map.initial;
    camera = new Camera(
      queryNumber("x", initial.x),
      queryNumber("y", initial.y),
      Math.max(0.125, Math.min(4, queryNumber("zoom", 1))),
    );
    floor = clampFloor(queryNumber("z", initial.z));

    const onDataChange = invalidate;
    chunkStore = new ChunkStore(manifest, ASSET_ROOT, onDataChange);
    overviewStore = new OverviewStore(manifest, ASSET_ROOT, onDataChange);
    detailRenderer = new DetailRenderer(elements.detail, things, ASSET_ROOT, onDataChange);
    creatureOverlayRenderer = new CreatureOverlayRenderer(elements.creatureLabels);
    overviewRenderer = new OverviewRenderer(elements.overview);

    bindEvents();
    resize();
    elements.loading.hidden = true;
    invalidate();
  } catch (error) {
    console.error(error);
    elements.loading.hidden = true;
    elements.error.hidden = false;
    elements.errorText.textContent = error?.message ?? String(error);
  }
}

boot();
