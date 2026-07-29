const TILE_PIXELS = 32;
export const DETAIL_ZOOM_THRESHOLD = 0.5;
export const CREATURE_LABEL_ZOOM_THRESHOLD = 0.85;

const OUTFIT_COLORS = [
  0xffffff, 0xffd4bf, 0xffe9bf, 0xffffbf, 0xe9ffbf, 0xd4ffbf, 0xbfffbf, 0xbfffd4,
  0xbfffe9, 0xbfffff, 0xbfe9ff, 0xbfd4ff, 0xbfbfff, 0xd4bfff, 0xe9bfff, 0xffbfff,
  0xffbfe9, 0xffbfd4, 0xffbfbf, 0xdadada, 0xbf9f8f, 0xbfaf8f, 0xbfbf8f, 0xafbf8f,
  0x9fbf8f, 0x8fbf8f, 0x8fbf9f, 0x8fbfaf, 0x8fbfbf, 0x8fafbf, 0x8f9fbf, 0x8f8fbf,
  0x9f8fbf, 0xaf8fbf, 0xbf8fbf, 0xbf8faf, 0xbf8f9f, 0xbf8f8f, 0xb6b6b6, 0xbf7f5f,
  0xbfaf8f, 0xbfbf5f, 0x9fbf5f, 0x7fbf5f, 0x5fbf5f, 0x5fbf7f, 0x5fbf9f, 0x5fbfbf,
  0x5f9fbf, 0x5f7fbf, 0x5f5fbf, 0x7f5fbf, 0x9f5fbf, 0xbf5fbf, 0xbf5f9f, 0xbf5f7f,
  0xbf5f5f, 0x919191, 0xbf6a3f, 0xbf943f, 0xbfbf3f, 0x94bf3f, 0x6abf3f, 0x3fbf3f,
  0x3fbf6a, 0x3fbf94, 0x3fbfbf, 0x3f94bf, 0x3f6abf, 0x3f3fbf, 0x6a3fbf, 0x943fbf,
  0xbf3fbf, 0xbf3f94, 0xbf3f6a, 0xbf3f3f, 0x6d6d6d, 0xff5500, 0xffaa00, 0xffff00,
  0xaaff00, 0x54ff00, 0x00ff00, 0x00ff54, 0x00ffaa, 0x00ffff, 0x00a9ff, 0x0055ff,
  0x0000ff, 0x5500ff, 0xa900ff, 0xfe00ff, 0xff00aa, 0xff0055, 0xff0000, 0x484848,
  0xbf3f00, 0xbf7f00, 0xbfbf00, 0x7fbf00, 0x3fbf00, 0x00bf00, 0x00bf3f, 0x00bf7f,
  0x00bfbf, 0x007fbf, 0x003fbf, 0x0000bf, 0x3f00bf, 0x7f00bf, 0xbf00bf, 0xbf007f,
  0xbf003f, 0xbf0000, 0x242424, 0x7f2a00, 0x7f5500, 0x7f7f00, 0x557f00, 0x2a7f00,
  0x007f00, 0x007f2a, 0x007f55, 0x007f7f, 0x00547f, 0x002a7f, 0x00007f, 0x2a007f,
  0x54007f, 0x7f007f, 0x7f0055, 0x7f002a, 0x7f0000,
];

function modulo(value, divisor) {
  const safe = Math.max(1, divisor);
  return ((value % safe) + safe) % safe;
}

function hashPosition(x, y, id) {
  return ((Math.imul(x, 73856093) ^ Math.imul(y, 19349663) ^ Math.imul(id, 83492791)) >>> 0);
}

export class Camera {
  constructor(x, y, zoom = 1) {
    this.centerX = x * TILE_PIXELS + TILE_PIXELS / 2;
    this.centerY = y * TILE_PIXELS + TILE_PIXELS / 2;
    this.zoom = zoom;
    this.width = 1;
    this.height = 1;
  }

  resize(width, height) {
    this.width = Math.max(1, width);
    this.height = Math.max(1, height);
  }

  centerOn(x, y) {
    this.centerX = x * TILE_PIXELS + TILE_PIXELS / 2;
    this.centerY = y * TILE_PIXELS + TILE_PIXELS / 2;
  }

  panScreen(deltaX, deltaY) {
    this.centerX -= deltaX / this.zoom;
    this.centerY -= deltaY / this.zoom;
  }

  zoomAt(factor, screenX, screenY) {
    const before = this.screenToWorld(screenX, screenY);
    this.zoom = Math.max(0.125, Math.min(4, this.zoom * factor));
    const after = this.screenToWorld(screenX, screenY);
    this.centerX += before.x - after.x;
    this.centerY += before.y - after.y;
  }

  screenToWorld(screenX, screenY) {
    return {
      x: this.centerX + (screenX - this.width / 2) / this.zoom,
      y: this.centerY + (screenY - this.height / 2) / this.zoom,
    };
  }

  screenToTile(screenX, screenY) {
    const world = this.screenToWorld(screenX, screenY);
    return { x: Math.floor(world.x / TILE_PIXELS), y: Math.floor(world.y / TILE_PIXELS) };
  }

  visibleTileBounds(margin = 3) {
    const halfWidth = this.width / (2 * this.zoom * TILE_PIXELS);
    const halfHeight = this.height / (2 * this.zoom * TILE_PIXELS);
    const centerTileX = this.centerX / TILE_PIXELS;
    const centerTileY = this.centerY / TILE_PIXELS;
    return {
      left: Math.floor(centerTileX - halfWidth) - margin,
      top: Math.floor(centerTileY - halfHeight) - margin,
      right: Math.ceil(centerTileX + halfWidth) + margin,
      bottom: Math.ceil(centerTileY + halfHeight) + margin,
    };
  }

  get tileX() {
    return Math.floor(this.centerX / TILE_PIXELS);
  }

  get tileY() {
    return Math.floor(this.centerY / TILE_PIXELS);
  }
}

function compileShader(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const message = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(`Błąd shadera: ${message}`);
  }
  return shader;
}

function createProgram(gl, vertexSource, fragmentSource) {
  const program = gl.createProgram();
  gl.attachShader(program, compileShader(gl, gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(program, compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(`Błąd linkowania WebGL: ${gl.getProgramInfoLog(program)}`);
  }
  return program;
}

class AtlasTexture {
  constructor(gl, descriptor, assetRoot, onChange) {
    this.gl = gl;
    this.descriptor = descriptor;
    this.assetRoot = assetRoot;
    this.onChange = onChange;
    this.loaded = new Set();
    this.pending = new Map();
    this.texture = gl.createTexture();
    const maxLayers = gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS);
    if (descriptor.pages.length > maxLayers) {
      throw new Error(`Karta graficzna obsługuje ${maxLayers} warstw atlasu, potrzeba ${descriptor.pages.length}.`);
    }
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.texture);
    gl.texStorage3D(
      gl.TEXTURE_2D_ARRAY,
      1,
      gl.RGBA8,
      descriptor.size,
      descriptor.size,
      descriptor.pages.length,
    );
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D_ARRAY, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  }

  ensure(page) {
    if (page < 0 || this.loaded.has(page)) return Promise.resolve();
    if (this.pending.has(page)) return this.pending.get(page);
    const url = new URL(this.descriptor.pages[page], this.assetRoot);
    const promise = fetch(url)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}: ${url}`);
        return response.blob();
      })
      .then((blob) => createImageBitmap(blob, { premultiplyAlpha: "none" }))
      .then((image) => {
        const gl = this.gl;
        gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.texture);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
        gl.texSubImage3D(
          gl.TEXTURE_2D_ARRAY,
          0,
          0,
          0,
          page,
          this.descriptor.size,
          this.descriptor.size,
          1,
          gl.RGBA,
          gl.UNSIGNED_BYTE,
          image,
        );
        image.close();
        this.loaded.add(page);
        this.pending.delete(page);
        this.onChange();
      })
      .catch((error) => {
        this.pending.delete(page);
        throw error;
      });
    this.pending.set(page, promise);
    return promise;
  }
}

class ThingResolver {
  constructor(things) {
    this.things = things;
    this.cache = new WeakMap();
  }

  groupFor(definition, useMovingGroup) {
    if (!definition?.groups?.length) return null;
    if (definition.category === "creature") {
      if (useMovingGroup) {
        const moving = definition.groups.find((group) => group.type === 1 && group.frames > 1);
        if (moving) return moving;
      }
      return definition.groups.find((group) => group.type === 0) ?? definition.groups[0];
    }
    return definition.groups[0];
  }

  phaseAt(group, timeMs, seed, enabled) {
    if (!enabled || group.frames <= 1) return 0;
    const durations = group.durations?.length ? group.durations : new Array(group.frames).fill(250);
    const pingPong = group.loop < 0 && group.frames > 2;
    const sequence = [];
    for (let phase = 0; phase < group.frames; phase += 1) sequence.push(phase);
    if (pingPong) {
      for (let phase = group.frames - 2; phase > 0; phase -= 1) sequence.push(phase);
    }
    let cycle = 0;
    for (const phase of sequence) cycle += Math.max(1, durations[phase] ?? 250);
    let cursor = (timeMs + (group.async ? seed % cycle : 0)) % cycle;
    for (const phase of sequence) {
      const duration = Math.max(1, durations[phase] ?? 250);
      if (cursor < duration) return phase;
      cursor -= duration;
    }
    return 0;
  }

  itemParts(group, patternX, patternY, frame, subtype) {
    let groupCache = this.cache.get(group);
    if (!groupCache) {
      groupCache = new Map();
      this.cache.set(group, groupCache);
    }
    const key = `i/${patternX}/${patternY}/${frame}/${subtype}`;
    if (groupCache.has(key)) return groupCache.get(key);
    const parts = [];
    const count = group.sprites.length;
    for (let spriteX = 0; spriteX < group.width; spriteX += 1) {
      for (let spriteY = 0; spriteY < group.height; spriteY += 1) {
        for (let layer = 0; layer < group.layers; layer += 1) {
          let index;
          if (subtype >= 0 && group.width <= 1 && group.height <= 1) {
            index = subtype;
          } else {
            index =
              (((((modulo(frame, group.frames) * group.patternY + modulo(patternY, group.patternY)) *
                group.patternX +
                modulo(patternX, group.patternX)) *
                group.layers +
                layer) *
                group.height +
                spriteY) *
                group.width +
                spriteX);
          }
          index = modulo(index, count);
          const ref = group.sprites[index] ?? -1;
          if (ref >= 0) {
            parts.push({
              ref,
              mask: -1,
              x: -spriteX * TILE_PIXELS,
              y: -spriteY * TILE_PIXELS,
            });
          }
        }
      }
    }
    groupCache.set(key, parts);
    return parts;
  }

  creatureParts(group, direction, frame, addon, mounted) {
    let groupCache = this.cache.get(group);
    if (!groupCache) {
      groupCache = new Map();
      this.cache.set(group, groupCache);
    }
    const patternZ = mounted && group.patternZ > 1 ? 1 : 0;
    const key = `c/${direction}/${frame}/${addon}/${patternZ}`;
    if (groupCache.has(key)) return groupCache.get(key);
    const parts = [];
    const patternYs = [0];
    for (let patternY = 1; patternY < group.patternY; patternY += 1) {
      if ((addon & (1 << (patternY - 1))) !== 0) patternYs.push(patternY);
    }
    const indexFor = (spriteX, spriteY, layer, patternY) =>
      ((((((modulo(frame, group.frames) * group.patternZ + patternZ) * group.patternY + patternY) *
        group.patternX +
        modulo(direction, group.patternX)) *
        group.layers +
        layer) *
        group.height +
        spriteY) *
        group.width +
        spriteX);

    for (let spriteX = 0; spriteX < group.width; spriteX += 1) {
      for (let spriteY = 0; spriteY < group.height; spriteY += 1) {
        for (const patternY of patternYs) {
          const x = -spriteX * TILE_PIXELS;
          const y = -spriteY * TILE_PIXELS;
          if (group.layers > 1) {
            const base = group.sprites[modulo(indexFor(spriteX, spriteY, 0, patternY), group.sprites.length)] ?? -1;
            const mask = group.sprites[modulo(indexFor(spriteX, spriteY, 1, patternY), group.sprites.length)] ?? -1;
            if (base >= 0) parts.push({ ref: base, mask, x, y });
            for (let layer = 2; layer < group.layers; layer += 1) {
              const ref = group.sprites[modulo(indexFor(spriteX, spriteY, layer, patternY), group.sprites.length)] ?? -1;
              if (ref >= 0) parts.push({ ref, mask: -1, x, y });
            }
          } else {
            const ref = group.sprites[modulo(indexFor(spriteX, spriteY, 0, patternY), group.sprites.length)] ?? -1;
            if (ref >= 0) parts.push({ ref, mask: -1, x, y });
          }
        }
      }
    }
    groupCache.set(key, parts);
    return parts;
  }
}

const VERTEX_SHADER = `#version 300 es
precision highp float;
layout(location=0) in vec2 aWorld;
layout(location=1) in vec3 aSprite;
layout(location=2) in vec3 aMask;
layout(location=3) in vec4 aColors;
uniform vec2 uCamera;
uniform vec2 uViewport;
uniform float uZoom;
uniform float uCellSize;
uniform float uAtlasSize;
out vec2 vUv;
out vec2 vMaskUv;
flat out float vPage;
flat out float vMaskPage;
flat out vec4 vColors;

void main() {
  vec2 corners[6] = vec2[6](
    vec2(0.0, 0.0), vec2(1.0, 0.0), vec2(0.0, 1.0),
    vec2(0.0, 1.0), vec2(1.0, 0.0), vec2(1.0, 1.0)
  );
  vec2 corner = corners[gl_VertexID];
  vec2 screen = (aWorld + corner * 32.0 - uCamera) * uZoom + uViewport * 0.5;
  vec2 clip = vec2(screen.x / uViewport.x * 2.0 - 1.0, 1.0 - screen.y / uViewport.y * 2.0);
  gl_Position = vec4(clip, 0.0, 1.0);
  vec2 pixel = corner * (uCellSize - 1.0) + vec2(0.5);
  vUv = (aSprite.xy * uCellSize + pixel) / uAtlasSize;
  vMaskUv = (aMask.xy * uCellSize + pixel) / uAtlasSize;
  vPage = aSprite.z;
  vMaskPage = aMask.z;
  vColors = aColors;
}`;

const FRAGMENT_SHADER = `#version 300 es
precision highp float;
uniform highp sampler2DArray uAtlas;
uniform sampler2D uPalette;
in vec2 vUv;
in vec2 vMaskUv;
flat in float vPage;
flat in float vMaskPage;
flat in vec4 vColors;
out vec4 outColor;

vec3 outfitColor(float index) {
  int safeIndex = clamp(int(index + 0.5), 0, 132);
  return texelFetch(uPalette, ivec2(safeIndex, 0), 0).rgb;
}

void main() {
  vec4 base = texture(uAtlas, vec3(vUv, vPage));
  if (base.a <= 0.001) discard;
  if (vMaskPage >= 0.0) {
    vec4 mask = texture(uAtlas, vec3(vMaskUv, vMaskPage));
    if (mask.a > 0.001) {
      vec3 tint = vec3(0.0);
      if (mask.r > 0.01 && mask.g > 0.01 && mask.b <= 0.01) tint = outfitColor(vColors.x);
      else if (mask.r > 0.01 && mask.g <= 0.01 && mask.b <= 0.01) tint = outfitColor(vColors.y);
      else if (mask.r <= 0.01 && mask.g > 0.01 && mask.b <= 0.01) tint = outfitColor(vColors.z);
      else if (mask.r <= 0.01 && mask.g <= 0.01 && mask.b > 0.01) tint = outfitColor(vColors.w);
      if (dot(tint, tint) > 0.0) base.rgb *= tint;
    }
  }
  outColor = base;
}`;

export class DetailRenderer {
  constructor(canvas, things, assetRoot, onChange) {
    this.canvas = canvas;
    this.things = things;
    this.assetRoot = assetRoot;
    this.onChange = onChange;
    this.resolver = new ThingResolver(things);
    this.gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      depth: false,
      premultipliedAlpha: false,
      powerPreference: "high-performance",
    });
    if (!this.gl) {
      throw new Error("WebGL 2 nie jest dostępny w tej przeglądarce.");
    }
    const gl = this.gl;
    this.program = createProgram(gl, VERTEX_SHADER, FRAGMENT_SHADER);
    this.atlas = new AtlasTexture(gl, things.atlas, assetRoot, onChange);
    this.instanceBuffer = gl.createBuffer();
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuffer);
    const stride = 12 * 4;
    for (const [location, size, offset] of [
      [0, 2, 0],
      [1, 3, 2 * 4],
      [2, 3, 5 * 4],
      [3, 4, 8 * 4],
    ]) {
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, gl.FLOAT, false, stride, offset);
      gl.vertexAttribDivisor(location, 1);
    }
    this.paletteTexture = this.createPaletteTexture();
    this.instanceValues = [];
    this.lastInstanceCount = 0;
  }

  createPaletteTexture() {
    const gl = this.gl;
    const pixels = new Uint8Array(OUTFIT_COLORS.length * 4);
    OUTFIT_COLORS.forEach((color, index) => {
      pixels[index * 4] = (color >> 16) & 0xff;
      pixels[index * 4 + 1] = (color >> 8) & 0xff;
      pixels[index * 4 + 2] = color & 0xff;
      pixels[index * 4 + 3] = 255;
    });
    const texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, OUTFIT_COLORS.length, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    return texture;
  }

  resize(width, height, dpr) {
    const physicalWidth = Math.max(1, Math.round(width * dpr));
    const physicalHeight = Math.max(1, Math.round(height * dpr));
    if (this.canvas.width !== physicalWidth || this.canvas.height !== physicalHeight) {
      this.canvas.width = physicalWidth;
      this.canvas.height = physicalHeight;
    }
  }

  spriteCell(ref) {
    const descriptor = this.things.atlas;
    const page = Math.floor(ref / descriptor.capacity);
    const cell = ref % descriptor.capacity;
    return [cell % descriptor.cellsPerRow, Math.floor(cell / descriptor.cellsPerRow), page];
  }

  appendPart(worldX, worldY, part, colors) {
    const sprite = this.spriteCell(part.ref);
    const mask = part.mask >= 0 ? this.spriteCell(part.mask) : [0, 0, -1];
    this.atlas.ensure(sprite[2]).catch(console.error);
    if (mask[2] >= 0) this.atlas.ensure(mask[2]).catch(console.error);
    this.instanceValues.push(
      worldX + part.x,
      worldY + part.y,
      sprite[0],
      sprite[1],
      sprite[2],
      mask[0],
      mask[1],
      mask[2],
      colors[0],
      colors[1],
      colors[2],
      colors[3],
    );
  }

  renderItem(tile, entry, definition, cursor, timeMs, options, hookSide) {
    const group = this.resolver.groupFor(definition, false);
    if (!group) return;
    let patternX = modulo(tile.x, group.patternX);
    let patternY = modulo(tile.y, group.patternY);
    let subtype = -1;
    if (definition.flags.fluid) {
      subtype = Math.max(0, entry.value);
    } else if (definition.flags.hangable) {
      if (hookSide === "south" && group.patternX > 1) patternX = 1;
      else if (hookSide === "east" && group.patternX > 2) patternX = 2;
      else patternX = 0;
      patternY = 0;
    } else if (definition.flags.stackable && group.patternX === 4 && group.patternY === 2) {
      const count = Math.max(0, entry.value);
      patternX = count <= 0 ? 0 : count < 5 ? count - 1 : count < 10 ? 0 : count < 25 ? 1 : count < 50 ? 2 : 3;
      patternY = count < 5 ? 0 : 1;
    }
    const seed = hashPosition(tile.x, tile.y, entry.id);
    const frame = this.resolver.phaseAt(group, timeMs, seed, options.animations);
    const parts = this.resolver.itemParts(group, patternX, patternY, frame, subtype);
    const baseX = cursor.x - definition.offsetX;
    const baseY = cursor.y - definition.offsetY;
    for (const part of parts) this.appendPart(baseX, baseY, part, [0, 0, 0, 0]);
  }

  renderCreature(tile, entry, cursor, timeMs, options) {
    const creature = this.things.creatures[entry.id];
    if (!creature?.thing || !options.creatures) return;
    const group = this.resolver.groupFor(creature.thing, options.movingCreatureFrames);
    if (!group) return;
    const seed = hashPosition(tile.x, tile.y, entry.id + 65536);
    const frame = this.resolver.phaseAt(group, timeMs, seed, options.animations);
    const parts = this.resolver.creatureParts(
      group,
      entry.value & 0xff,
      frame,
      creature.lookAddon,
      creature.lookMount !== 0,
    );
    const baseX = cursor.x - creature.thing.offsetX;
    const baseY = cursor.y - creature.thing.offsetY;
    for (const part of parts) this.appendPart(baseX, baseY, part, creature.colors);
  }

  render(camera, tiles, timeMs, options, dpr) {
    this.instanceValues.length = 0;
    const sortedTiles = tiles.slice().sort(
      (a, b) =>
        (a.floorOrder ?? 0) - (b.floorOrder ?? 0) ||
        (a.drawY ?? a.y) - (b.drawY ?? b.y) ||
        (a.drawX ?? a.x) - (b.drawX ?? b.x),
    );
    for (const tile of sortedTiles) {
      const itemEntries = tile.entries
        .filter((entry) => entry.kind === 0)
        .map((entry) => ({ entry, definition: this.things.items[String(entry.id)] }))
        .filter((value) => value.definition);
      const ground = itemEntries.filter(({ definition }) => definition.flags.ground);
      const border = itemEntries.filter(({ definition }) => !definition.flags.ground && definition.flags.border);
      const common = itemEntries.filter(({ definition }) => !definition.flags.ground && !definition.flags.border);
      const cursor = {
        x: (tile.drawX ?? tile.x) * TILE_PIXELS,
        y: (tile.drawY ?? tile.y) * TILE_PIXELS,
      };

      for (const { entry, definition } of ground) {
        this.renderItem(tile, entry, definition, cursor, timeMs, options, "none");
      }
      const hook = itemEntries.find(({ definition }) => definition.flags.hookSouth || definition.flags.hookEast);
      const hookSide = hook?.definition.flags.hookSouth ? "south" : hook?.definition.flags.hookEast ? "east" : "none";
      for (const { entry, definition } of [...border, ...common]) {
        this.renderItem(tile, entry, definition, cursor, timeMs, options, hookSide);
        if (definition.flags.hasElevation && definition.elevation > 0) {
          cursor.x -= definition.elevation;
          cursor.y -= definition.elevation;
        }
      }
      for (const entry of tile.entries.filter((value) => value.kind === 1)) {
        this.renderCreature(tile, entry, cursor, timeMs, options);
      }
    }

    const gl = this.gl;
    const values = new Float32Array(this.instanceValues);
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.disable(gl.DEPTH_TEST);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0.055, 0.063, 0.067, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.program);
    gl.bindVertexArray(this.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.instanceBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, values, gl.DYNAMIC_DRAW);
    gl.uniform2f(gl.getUniformLocation(this.program, "uCamera"), camera.centerX, camera.centerY);
    gl.uniform2f(gl.getUniformLocation(this.program, "uViewport"), this.canvas.width, this.canvas.height);
    gl.uniform1f(gl.getUniformLocation(this.program, "uZoom"), camera.zoom * dpr);
    gl.uniform1f(gl.getUniformLocation(this.program, "uCellSize"), this.things.atlas.cellSize);
    gl.uniform1f(gl.getUniformLocation(this.program, "uAtlasSize"), this.things.atlas.size);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D_ARRAY, this.atlas.texture);
    gl.uniform1i(gl.getUniformLocation(this.program, "uAtlas"), 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.paletteTexture);
    gl.uniform1i(gl.getUniformLocation(this.program, "uPalette"), 1);
    gl.drawArraysInstanced(gl.TRIANGLES, 0, 6, values.length / 12);
    this.lastInstanceCount = values.length / 12;
    return this.lastInstanceCount;
  }
}

export class CreatureOverlayRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
  }

  resize(width, height, dpr) {
    const physicalWidth = Math.max(1, Math.round(width * dpr));
    const physicalHeight = Math.max(1, Math.round(height * dpr));
    if (this.canvas.width !== physicalWidth || this.canvas.height !== physicalHeight) {
      this.canvas.width = physicalWidth;
      this.canvas.height = physicalHeight;
    }
  }

  clear(camera, dpr) {
    this.context.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.context.clearRect(0, 0, camera.width, camera.height);
  }

  screenPoint(camera, worldX, worldY) {
    return {
      x: (worldX - camera.centerX) * camera.zoom + camera.width / 2,
      y: (worldY - camera.centerY) * camera.zoom + camera.height / 2,
    };
  }

  drawFocus(camera, focus, timeMs) {
    if (!focus) return;
    const point = this.screenPoint(
      camera,
      (focus.x + 0.5) * TILE_PIXELS,
      (focus.y + 0.5) * TILE_PIXELS,
    );
    if (point.x < -40 || point.y < -40 || point.x > camera.width + 40 || point.y > camera.height + 40) return;
    const context = this.context;
    const pulse = 1 + Math.sin(timeMs / 180) * 0.12;
    const radius = Math.max(11, TILE_PIXELS * camera.zoom * 0.46) * pulse;
    context.save();
    context.strokeStyle = "rgba(240, 189, 120, 0.95)";
    context.fillStyle = "rgba(212, 154, 82, 0.12)";
    context.lineWidth = 2;
    context.shadowColor = "rgba(240, 189, 120, 0.5)";
    context.shadowBlur = 9;
    context.beginPath();
    context.arc(point.x, point.y, radius, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    context.restore();
  }

  render(camera, tiles, things, options, dpr, focus, timeMs) {
    this.clear(camera, dpr);
    if (!options.creatures) return;
    this.drawFocus(camera, focus, timeMs);
    if (!options.creatureNames || camera.zoom < CREATURE_LABEL_ZOOM_THRESHOLD) return;

    const context = this.context;
    context.font = "700 12px Inter, ui-sans-serif, system-ui, sans-serif";
    context.textAlign = "center";
    context.textBaseline = "bottom";
    context.lineJoin = "round";
    const labels = [];

    for (const tile of tiles) {
      const drawX = tile.drawX ?? tile.x;
      const drawY = tile.drawY ?? tile.y;
      for (const entry of tile.entries) {
        if (entry.kind !== 1) continue;
        const creature = things.creatures[entry.id];
        if (!creature?.thing) continue;
        const height = Math.max(1, ...creature.thing.groups.map((group) => group.height ?? 1));
        const point = this.screenPoint(
          camera,
          (drawX + 0.5) * TILE_PIXELS,
          drawY * TILE_PIXELS - (height - 1) * TILE_PIXELS - creature.thing.offsetY - 5,
        );
        if (point.x < -120 || point.x > camera.width + 120 || point.y < -30 || point.y > camera.height + 30) {
          continue;
        }
        labels.push({
          name: creature.name,
          x: point.x,
          y: point.y,
          focused:
            focus?.x === tile.x &&
            focus?.y === tile.y &&
            focus?.z === tile.z &&
            focus?.creatureId === entry.id,
        });
      }
    }

    labels.sort((a, b) => Number(a.focused) - Number(b.focused));
    const occupied = [];
    for (const label of labels) {
      const width = Math.ceil(context.measureText(label.name).width) + 10;
      const box = {
        left: label.x - width / 2,
        right: label.x + width / 2,
        top: label.y - 16,
        bottom: label.y + 2,
      };
      const collides = occupied.some(
        (other) =>
          box.left < other.right &&
          box.right > other.left &&
          box.top < other.bottom &&
          box.bottom > other.top,
      );
      if (collides && !label.focused) continue;
      occupied.push(box);
      context.lineWidth = label.focused ? 4.5 : 3.5;
      context.strokeStyle = "rgba(7, 10, 8, 0.92)";
      context.fillStyle = label.focused ? "#f0bd78" : "#eef3ea";
      context.strokeText(label.name, label.x, label.y);
      context.fillText(label.name, label.x, label.y);
    }
  }
}

export class OverviewRenderer {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d", { alpha: false });
    this.context.imageSmoothingEnabled = false;
  }

  resize(width, height, dpr) {
    const physicalWidth = Math.max(1, Math.round(width * dpr));
    const physicalHeight = Math.max(1, Math.round(height * dpr));
    if (this.canvas.width !== physicalWidth || this.canvas.height !== physicalHeight) {
      this.canvas.width = physicalWidth;
      this.canvas.height = physicalHeight;
    }
  }

  render(camera, images, chunkSize, dpr) {
    const context = this.context;
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.fillStyle = "#0e1011";
    context.fillRect(0, 0, camera.width, camera.height);
    context.imageSmoothingEnabled = false;
    const worldChunkPixels = chunkSize * TILE_PIXELS;
    const drawSize = worldChunkPixels * camera.zoom;
    for (const { x, y, image, drawOffsetTiles = 0 } of images) {
      const worldX = (x * chunkSize + drawOffsetTiles) * TILE_PIXELS;
      const worldY = (y * chunkSize + drawOffsetTiles) * TILE_PIXELS;
      const screenX = (worldX - camera.centerX) * camera.zoom + camera.width / 2;
      const screenY = (worldY - camera.centerY) * camera.zoom + camera.height / 2;
      context.drawImage(image, Math.round(screenX), Math.round(screenY), Math.ceil(drawSize), Math.ceil(drawSize));
    }
  }
}
