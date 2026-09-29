// 3D "board game" view of any map: regions are extruded tiles on a wooden board, the leader of each
// region gets a pawn in their color (bigger pawn = more seats). Same interface as MapView (paint/dispose).
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { project, polygons, labelPoint, labelScale } from './map.js';

const TILE = 6;          // tile thickness
const NEUTRAL_2D = '#2a3244';     // "nobody" colour used by the 2D map ...
const NATURAL_WOOD = '#e2d3b8';   // ... shown as pale unpainted birch, so every party colour stands out

// Procedural wood grain (no image files, works offline). Light, near-white grain so it can be
// multiplied with any paint colour; `tint` bakes a colour in for the board itself.
function woodCanvas(tint = null, { size = 512, rings = 38, seed = 1 } = {}) {
  let r = seed;
  const rand = () => (r = (r * 16807) % 2147483647) / 2147483647;
  const c = document.createElement('canvas');
  c.width = c.height = size;
  const ctx = c.getContext('2d');
  ctx.fillStyle = tint || '#f3e6d0';
  ctx.fillRect(0, 0, size, size);
  // long grain lines with gentle waves
  for (let i = 0; i < rings * 4; i++) {
    const y0 = rand() * size, amp = 2 + rand() * 7, freq = (1 + rand() * 2) * Math.PI * 2 / size, ph = rand() * 6;
    ctx.strokeStyle = `rgba(${tint ? '20,10,0' : '120,80,40'},${0.04 + rand() * 0.12})`;
    ctx.lineWidth = 0.6 + rand() * 2.2;
    ctx.beginPath();
    for (let x = -4; x <= size + 4; x += 4) {
      const y = y0 + Math.sin(x * freq + ph) * amp + Math.sin(x * freq * 3.1 + ph) * amp * 0.25;
      x < 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  // a few knots
  for (let k = 0; k < 3; k++) {
    const x = rand() * size, y = rand() * size, rx = 6 + rand() * 10;
    for (let j = 6; j > 0; j--) {
      ctx.strokeStyle = `rgba(${tint ? '15,8,0' : '110,70,30'},${0.08 + 0.03 * j})`;
      ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.ellipse(x, y, rx * j / 3, rx * j / 9, 0, 0, Math.PI * 2); ctx.stroke();
    }
  }
  // fine pores
  const img = ctx.getImageData(0, 0, size, size), d = img.data;
  for (let i = 0; i < d.length; i += 4) { const n = (rand() - 0.5) * 14; d[i] += n; d[i + 1] += n; d[i + 2] += n; }
  ctx.putImageData(img, 0, 0);
  return c;
}

function woodTexture(canvas, repeat, color = true) {
  const t = new THREE.CanvasTexture(canvas);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat, repeat);
  t.anisotropy = 8;
  if (color) t.colorSpace = THREE.SRGBColorSpace;
  return t;
}
const LIFT = 6;          // how far the selected tile rises
const LED = 3;           // regions with a leader sit a bit higher, like placed pieces

function pawnGeometry() {
  // classic pawn silhouette, revolved
  const pts = [[0, 0], [6, 0], [6, 1.6], [4.6, 2.6], [3.6, 3.4], [2.3, 8.5], [3.8, 9.6], [3.8, 10.6], [2.2, 11.2], [0, 11.2]]
    .map(([x, y]) => new THREE.Vector2(x, y));
  const body = new THREE.LatheGeometry(pts, 24);
  const head = new THREE.SphereGeometry(3.3, 20, 14);
  head.translate(0, 13.6, 0);
  return [body, head];
}

function textSprite(text, k = 1) {
  const c = document.createElement('canvas');
  const ctx = c.getContext('2d');
  const font = 'bold 44px Inter, system-ui, sans-serif';
  ctx.font = font;
  c.width = Math.ceil(ctx.measureText(text).width) + 24; c.height = 64;
  ctx.font = font; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.lineWidth = 8; ctx.strokeStyle = 'rgba(0,0,0,.75)'; ctx.fillStyle = '#fff';
  ctx.strokeText(text, c.width / 2, 34); ctx.fillText(text, c.width / 2, 34);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }));
  s.scale.set(c.width / 64 * 11 * k, 11 * k, 1);
  s.renderOrder = 10;
  return s;
}

export class Board3D {
  constructor(container, map, { onSelect, tooltip }) {
    this.map = map;
    this.tooltip = tooltip;
    const proj = project(map);
    const k = this.k = labelScale(proj);  // scale labels/pawns up on tall maps
    const [cx, cy] = [proj.W / 2, proj.H / 2];
    const toXY = p => { const [x, y] = proj.fn(p); return new THREE.Vector2(x - cx, -(y - cy)); };
    const toWorld = ([x, y]) => [x - cx, y - cy];  // already-projected screen point -> x/z

    this.el = document.createElement('div');
    this.el.className = 'board3d';
    container.appendChild(this.el);

    const renderer = this.renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.1;
    this.el.appendChild(renderer.domElement);

    const scene = this.scene = new THREE.Scene();
    scene.background = new THREE.Color('#0f1420');
    const camera = this.camera = new THREE.PerspectiveCamera(38, 1, 1, 6000);
    this.half = [proj.W / 2 + 60, proj.H / 2 + 60];  // board half-size, used to fit the camera
    const controls = this.controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI / 2.25;
    controls.minDistance = 150;
    controls.maxDistance = 2200;
    controls.addEventListener('start', () => { this.touched = true; });  // stop auto-fitting once the user moves the camera

    scene.add(new THREE.HemisphereLight(0xfff1dc, 0x3a2a1c, 1.4));   // warm table-lamp light
    const sun = new THREE.DirectionalLight(0xffe6c4, 2.4);
    sun.position.set(-proj.W * 0.35, 800, proj.H * 0.6);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    Object.assign(sun.shadow.camera, { left: -cx - 60, right: cx + 60, top: cy + 60, bottom: -cy - 60, near: 10, far: 2500 });
    scene.add(sun);

    // the board: wooden frame + felt
    // the board: walnut frame + darker stained playing surface, both with grain
    const walnut = woodCanvas('#5a3a22', { seed: 7 });
    const frameTex = woodTexture(walnut, 3), frameBump = woodTexture(walnut, 3, false);
    const wood = new THREE.Mesh(new THREE.BoxGeometry(proj.W + 120, 24, proj.H + 120),
      new THREE.MeshStandardMaterial({ map: frameTex, bumpMap: frameBump, bumpScale: 1.5, roughness: 0.7, metalness: 0 }));
    wood.position.y = -12; wood.receiveShadow = true;
    const stain = woodCanvas('#3b2718', { seed: 3, rings: 30 });
    const felt = new THREE.Mesh(new THREE.BoxGeometry(proj.W + 70, 2, proj.H + 70),
      new THREE.MeshStandardMaterial({ map: woodTexture(stain, 4), bumpMap: woodTexture(stain, 4, false), bumpScale: 1, roughness: 0.85, metalness: 0 }));
    felt.position.y = 1; felt.receiveShadow = true;
    scene.add(wood, felt);

    // region tiles
    this.tiles = {};
    this.labels = {};
    this.anchor = {};
    const edgeMat = new THREE.LineBasicMaterial({ color: 0x2a1a0e, transparent: true, opacity: 0.6 });
    // Extruded tiles get world-unit UVs, so one grain texture repeated every ~150 units covers all tiles
    const grain = woodCanvas(null, { seed: 11 });
    this.tileMap = woodTexture(grain, 1 / 150);
    this.tileBump = woodTexture(grain, 1 / 150, false);
    this.pawnMap = woodTexture(woodCanvas(null, { seed: 5, rings: 20 }), 1);
    for (const r of map.regions) {
      const shapes = polygons(r.geometry).map(poly => {
        const shape = new THREE.Shape(poly[0].map(toXY));
        poly.slice(1).forEach(h => shape.holes.push(new THREE.Path(h.map(toXY))));
        return shape;
      });
      const geo = new THREE.ExtrudeGeometry(shapes, { depth: TILE, bevelEnabled: false });
      geo.rotateX(-Math.PI / 2);
      const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
        color: NATURAL_WOOD, map: this.tileMap, bumpMap: this.tileBump, bumpScale: 0.8, roughness: 0.8, metalness: 0 }));
      mesh.position.y = 2;
      mesh.castShadow = mesh.receiveShadow = true;
      mesh.userData.id = r.id;
      mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo, 40), edgeMat));
      scene.add(mesh);
      this.tiles[r.id] = mesh;

      // tiny regions (e.g. DC) also get a clickable round pedestal at their callout spot
      let [ax, az] = toWorld(r.label ? proj.fn(r.label) : labelPoint(r.geometry, proj.fn));
      if (r.callout) {
        const [px, pz] = toWorld(proj.fn(r.callout));
        const ped = new THREE.Mesh(new THREE.CylinderGeometry(11 * k, 11 * k, TILE, 28), mesh.material);
        ped.position.set(px, 2 + TILE / 2, pz);
        ped.castShadow = true; ped.userData.id = r.id;
        scene.add(ped);
        mesh.userData.pedestal = ped;
        [ax, az] = [px, pz];
      }
      this.anchor[r.id] = [ax, az];
      const label = textSprite(r.abbr ? `${r.abbr} ${r.seats}` : String(r.seats), k);
      label.position.set(ax, 2 + TILE + 7, az);
      scene.add(label);
      this.labels[r.id] = label;
    }
    this.pickables = Object.values(this.tiles).flatMap(t => [t, t.userData.pedestal].filter(Boolean));

    // pawns
    this.pawnParts = pawnGeometry();
    this.pawns = new THREE.Group();
    scene.add(this.pawns);
    this.pawnKey = '';

    // picking: hover -> tooltip, click (not drag) -> select
    const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
    const pick = e => {
      const b = renderer.domElement.getBoundingClientRect();
      ndc.set(((e.clientX - b.left) / b.width) * 2 - 1, -((e.clientY - b.top) / b.height) * 2 + 1);
      ray.setFromCamera(ndc, camera);
      return ray.intersectObjects(this.pickables, false)[0]?.object.userData.id ?? null;
    };
    let down = null;
    const cvs = renderer.domElement;
    cvs.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
    cvs.addEventListener('pointerup', e => {
      if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) < 5) {
        const id = pick(e); if (id) onSelect(id);
      }
      down = null;
    });
    cvs.addEventListener('pointermove', e => {
      if (down) return;
      const id = pick(e);
      if (id !== this.hover) this.dirty = true;
      this.hover = id;
      cvs.style.cursor = id ? 'pointer' : 'grab';
      id ? tooltip(e, id) : tooltip(null);
    });
    cvs.addEventListener('pointerleave', () => { this.hover = null; tooltip(null); });

    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(this.el);
    this.resize();

    // render on demand: only while the camera moves or something is animating
    this.dirty = true;
    controls.addEventListener('change', () => { this.dirty = true; });
    const clock = new THREE.Clock();
    renderer.setAnimationLoop(() => {
      const dt = Math.min(clock.getDelta(), 0.05);
      let moving = false;
      for (const [id, t] of Object.entries(this.tiles)) {
        const target = 2 + (this.led?.has(id) ? LED : 0) + (id === this.selected ? LIFT : id === this.hover ? 2 : 0);
        const dy = target - t.position.y;
        if (Math.abs(dy) > 0.01) { t.position.y += dy * Math.min(1, dt * 12); moving = true; }
        this.labels[id].position.y = t.position.y + TILE + 7 + (this.labelBoost[id] || 0);
      }
      for (const p of this.pawns.children) {  // pawns drop onto the board
        const base = p.userData.rest + (this.tiles[p.userData.id].position.y - 2);
        if (p.position.y === base && !p.userData.vy) continue;
        p.userData.vy = (p.userData.vy || 0) - 900 * dt;
        p.position.y = Math.max(base, p.position.y + p.userData.vy * dt);
        if (p.position.y === base) p.userData.vy = 0;
        moving = true;
      }
      controls.update();
      if (!moving && !this.dirty) return;
      this.dirty = false;
      renderer.render(scene, camera);
    });
    this.labelBoost = {};
  }

  resize() {
    const w = this.el.clientWidth || 1, h = this.el.clientHeight || 1;
    this.renderer.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this.touched) {
      // fit the whole board, viewed from ~50° above the table
      const v = this.camera.fov * Math.PI / 360, hz = Math.atan(Math.tan(v) * this.camera.aspect);
      const d = Math.max(this.half[0] / Math.tan(hz), this.half[1] * 1.25 / Math.tan(v)) * 1.05;
      this.camera.position.set(0, d * 0.78, d * 0.63);
      this.controls.target.set(0, 0, 20);
    }
    this.dirty = true;
  }

  paint(colorOf, selected, game) {
    this.selected = selected;
    this.dirty = true;
    for (const [id, t] of Object.entries(this.tiles)) {
      const c = colorOf(id);
      t.material.color.set(c === NEUTRAL_2D ? NATURAL_WOOD : c);
      t.material.emissive.set(id === selected ? 0x333333 : 0x000000);
    }
    if (!game) return;
    this.led = new Set(Object.entries(game.regions).filter(([, g]) => g.leader).map(([id]) => id));
    const key = JSON.stringify(Object.entries(game.regions).map(([id, g]) => [id, g.leader]));
    if (key === this.pawnKey) return;
    const prev = new Map(this.pawns.children.map(p => [p.userData.id, p]));
    this.pawnKey = key;
    this.pawns.clear();
    this.labelBoost = {};
    for (const r of this.map.regions) {
      const leader = game.regions[r.id].leader;
      if (!leader) continue;
      const color = game.partyColor(leader);
      const old = prev.get(r.id);
      const s = (0.8 + Math.sqrt(r.seats) / 4) * this.k;
      const mat = new THREE.MeshStandardMaterial({ color, map: this.pawnMap, roughness: 0.6, metalness: 0 });  // painted wood
      const pawn = new THREE.Group();
      this.pawnParts.forEach(g => { const m = new THREE.Mesh(g, mat); m.castShadow = true; pawn.add(m); });
      pawn.scale.setScalar(s);
      const [x, z] = this.anchor[r.id];
      const rest = 2 + TILE;
      const same = old && old.userData.leader === leader;
      pawn.position.set(x, same ? old.position.y : rest + 120, z);  // new leader: drop in from above
      pawn.userData = { id: r.id, leader, rest, vy: same ? 0 : -50 };
      this.pawns.add(pawn);
      this.labelBoost[r.id] = 16.8 * s;
    }
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    this.resizeObs.disconnect();
    this.controls.dispose();
    this.scene.traverse(o => { o.geometry?.dispose(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { m.map?.dispose(); m.dispose(); }); });
    this.renderer.dispose();
    this.el.remove();
    this.tooltip(null);
  }
}
