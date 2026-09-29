// Renders any map JSON (Turkey, USA, ...) as an SVG. map.projection: "equirect" (lon/lat) or "planar" (pre-projected x/y).

const W = 1000;

export function project(map) {
  // Equirectangular with cos(mid-lat) horizontal scaling; good enough for a country-sized map.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const each = (coords, fn) => typeof coords[0] === 'number' ? fn(coords) : coords.forEach(c => each(c, fn));
  const grow = ([x, y]) => { minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y); };
  map.regions.forEach(r => { each(r.geometry.coordinates, grow); if (r.callout) grow(r.callout); });
  // "planar" maps are already projected (e.g. USA Albers); lon/lat maps get a cos(lat) correction
  const k = map.projection === 'planar' ? 1 : Math.cos(((minY + maxY) / 2) * Math.PI / 180);
  const scale = W / ((maxX - minX) * k);
  const H = (maxY - minY) * scale;
  return { fn: ([x, y]) => [(x - minX) * k * scale, (maxY - y) * scale], W, H };
}

function ringPath(ring, fn) {
  return ring.map((p, i) => { const [x, y] = fn(p); return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`; }).join('') + 'Z';
}

// Label/marker scale: wide maps (Turkey, USA) are shown width-limited at ~1:1; tall maps (Germany) get
// squeezed to the panel height, so their labels need to be drawn bigger to stay readable.
export const labelScale = proj => Math.max(1, 1.6 * proj.H / proj.W);

export function polygons(geom) {
  return geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
}

export function labelPoint(geom, fn) {
  // centroid-ish: bbox centre of the largest polygon's outer ring
  let best = null, bestArea = -1;
  for (const poly of polygons(geom)) {
    const pts = poly[0].map(fn);
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const area = (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
    if (area > bestArea) { bestArea = area; best = [(Math.max(...xs) + Math.min(...xs)) / 2, (Math.max(...ys) + Math.min(...ys)) / 2]; }
  }
  return best;
}

export class MapView {
  constructor(container, map, { onSelect, tooltip }) {
    this.map = map;
    const proj = project(map);
    const k = labelScale(proj);
    const svgNS = 'http://www.w3.org/2000/svg';
    this.svg = document.createElementNS(svgNS, 'svg');
    this.svg.setAttribute('class', 'map');
    const padRight = map.regions.some(r => r.callout) ? 40 * k : 10;  // room for callout boxes
    this.full = { x: -10, y: -10, w: proj.W + 10 + padRight, h: proj.H + 20 };
    this.vb = { ...this.full };
    this.svg.setAttribute('viewBox', `${this.vb.x} ${this.vb.y} ${this.vb.w} ${this.vb.h}`);
    this.k = k;
    this.paths = {};
    this.labels = {};
    this.boxes = {};
    for (const r of map.regions) {
      const d = polygons(r.geometry).map(poly => poly.map(ring => ringPath(ring, proj.fn)).join('')).join('');
      const path = document.createElementNS(svgNS, 'path');
      path.setAttribute('d', d);
      path.addEventListener('click', () => this.dragged || onSelect(r.id));
      path.addEventListener('pointermove', e => tooltip(e, r.id));
      path.addEventListener('mouseleave', () => tooltip(null));
      this.svg.appendChild(path);
      this.paths[r.id] = path;
    }
    for (const r of map.regions) {
      let [x, y] = r.label ? proj.fn(r.label) : labelPoint(r.geometry, proj.fn);
      if (r.callout) {
        // tiny region: clickable box off the coast, joined to the region by a leader line
        const [bx, by] = proj.fn(r.callout);
        const line = document.createElementNS(svgNS, 'line');
        Object.entries({ x1: x, y1: y, x2: bx - 16 * k, y2: by, class: 'leader' }).forEach(([a, v]) => line.setAttribute(a, v));
        const box = document.createElementNS(svgNS, 'rect');
        Object.entries({ x: bx - 16 * k, y: by - 9 * k, width: 44 * k, height: 18 * k, rx: 3 * k, class: 'callout' }).forEach(([a, v]) => box.setAttribute(a, v));
        box.addEventListener('click', () => this.dragged || onSelect(r.id));
        box.addEventListener('pointermove', e => tooltip(e, r.id));
        box.addEventListener('mouseleave', () => tooltip(null));
        this.svg.append(line, box);
        this.boxes[r.id] = box;
        [x, y] = [bx + 6 * k, by];
      }
      const t = document.createElementNS(svgNS, 'text');
      t.setAttribute('x', x); t.setAttribute('y', y + 3 * k);
      t.setAttribute('font-size', 9 * k);
      t.textContent = r.abbr ? `${r.abbr} ${r.seats}` : r.seats;
      this.svg.appendChild(t);
      this.labels[r.id] = t;
    }
    container.style.position = 'relative';
    container.appendChild(this.svg);
    this.zoomUi = document.createElement('div');
    this.zoomUi.className = 'zoom-ui';
    this.zoomUi.innerHTML = '<button title="Zoom in">+</button><button title="Zoom out">−</button><button title="Reset view">⟲</button>';
    const [zin, zout, zreset] = this.zoomUi.children;
    const centre = () => { const b = this.svg.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; };
    zin.onclick = () => this.zoomAt(...centre(), 1.5);
    zout.onclick = () => this.zoomAt(...centre(), 1 / 1.5);
    zreset.onclick = () => { this.vb = { ...this.full }; this.apply(); };
    container.appendChild(this.zoomUi);
    this.enablePanZoom();
  }

  // ---- pan & zoom (mouse wheel, drag, pinch) ----
  toSvg(cx, cy) {
    const p = this.svg.createSVGPoint();
    p.x = cx; p.y = cy;
    return p.matrixTransform(this.svg.getScreenCTM().inverse());
  }

  apply() {
    const f = this.full, v = this.vb;
    v.w = Math.min(f.w, Math.max(f.w / 10, v.w));
    v.h = v.w * f.h / f.w;
    v.x = Math.min(f.x + f.w - v.w, Math.max(f.x, v.x));
    v.y = Math.min(f.y + f.h - v.h, Math.max(f.y, v.y));
    this.svg.setAttribute('viewBox', `${v.x} ${v.y} ${v.w} ${v.h}`);
    // keep labels a constant on-screen size while zoomed in
    const z = v.w / f.w;
    for (const t of Object.values(this.labels)) t.setAttribute('font-size', 9 * this.k * Math.max(z, 0.35));
  }

  zoomAt(cx, cy, factor) {
    const before = this.toSvg(cx, cy);
    this.vb.w /= factor;
    this.apply();
    const after = this.toSvg(cx, cy);
    this.vb.x += before.x - after.x;
    this.vb.y += before.y - after.y;
    this.apply();
  }

  enablePanZoom() {
    const svg = this.svg, pts = new Map();
    let last = null, travel = 0;
    const mid = () => { const [a, b] = [...pts.values()]; return b ? [(a.x + b.x) / 2, (a.y + b.y) / 2, Math.hypot(a.x - b.x, a.y - b.y)] : [a.x, a.y, 0]; };
    svg.addEventListener('wheel', e => { e.preventDefault(); this.zoomAt(e.clientX, e.clientY, e.deltaY < 0 ? 1.2 : 1 / 1.2); }, { passive: false });
    svg.addEventListener('pointerdown', e => {
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (pts.size === 1) { travel = 0; this.dragged = false; }
      last = mid();
    });
    svg.addEventListener('pointermove', e => {
      if (!pts.has(e.pointerId)) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const cur = mid();
      travel += Math.hypot(cur[0] - last[0], cur[1] - last[1]);
      if (travel > 6) this.dragged = true;   // a drag, not a tap: don't select a region on release
      if (!this.dragged) return;
      if (pts.size === 2 && last[2] > 0) this.zoomAt(cur[0], cur[1], cur[2] / last[2]);
      const a = this.toSvg(last[0], last[1]), b = this.toSvg(cur[0], cur[1]);
      this.vb.x += a.x - b.x; this.vb.y += a.y - b.y;
      this.apply();
      last = cur;
    });
    const end = e => {
      pts.delete(e.pointerId);
      if (pts.size) last = mid();
      setTimeout(() => { if (!pts.size) this.dragged = false; }, 0);  // after the click event has fired
    };
    svg.addEventListener('pointerup', end);
    svg.addEventListener('pointercancel', end);
    svg.addEventListener('pointerleave', e => pts.has(e.pointerId) && end(e));
  }

  paint(colorOf, selected) {
    for (const [id, p] of Object.entries(this.paths)) {
      p.setAttribute('fill', colorOf(id));
      p.classList.toggle('sel', id === selected);
      if (id === selected) p.parentNode.appendChild(p); // bring outline to front
      const box = this.boxes[id];
      if (box) { box.setAttribute('fill', colorOf(id)); box.classList.toggle('sel', id === selected); }
    }
    for (const b of Object.values(this.boxes)) this.svg.appendChild(b);
    for (const t of Object.values(this.labels)) this.svg.appendChild(t);
  }
}
