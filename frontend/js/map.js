// Renders any map JSON (Turkey, USA, ...) as an SVG. map.projection: "equirect" (lon/lat) or "planar" (pre-projected x/y).

const W = 1000;

function project(map) {
  // Equirectangular with cos(mid-lat) horizontal scaling; good enough for a country-sized map.
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  const each = (coords, fn) => typeof coords[0] === 'number' ? fn(coords) : coords.forEach(c => each(c, fn));
  map.regions.forEach(r => each(r.geometry.coordinates, ([x, y]) => {
    minX = Math.min(minX, x); maxX = Math.max(maxX, x); minY = Math.min(minY, y); maxY = Math.max(maxY, y);
  }));
  // "planar" maps are already projected (e.g. USA Albers); lon/lat maps get a cos(lat) correction
  const k = map.projection === 'planar' ? 1 : Math.cos(((minY + maxY) / 2) * Math.PI / 180);
  const scale = W / ((maxX - minX) * k);
  const H = (maxY - minY) * scale;
  return { fn: ([x, y]) => [(x - minX) * k * scale, (maxY - y) * scale], W, H };
}

function ringPath(ring, fn) {
  return ring.map((p, i) => { const [x, y] = fn(p); return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`; }).join('') + 'Z';
}

function polygons(geom) {
  return geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
}

function labelPoint(geom, fn) {
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
    const svgNS = 'http://www.w3.org/2000/svg';
    this.svg = document.createElementNS(svgNS, 'svg');
    this.svg.setAttribute('class', 'map');
    this.svg.setAttribute('viewBox', `-10 -10 ${proj.W + 20} ${proj.H + 20}`);
    this.paths = {};
    this.labels = {};
    this.boxes = {};
    for (const r of map.regions) {
      const d = polygons(r.geometry).map(poly => poly.map(ring => ringPath(ring, proj.fn)).join('')).join('');
      const path = document.createElementNS(svgNS, 'path');
      path.setAttribute('d', d);
      path.addEventListener('click', () => onSelect(r.id));
      path.addEventListener('mousemove', e => tooltip(e, r.id));
      path.addEventListener('mouseleave', () => tooltip(null));
      this.svg.appendChild(path);
      this.paths[r.id] = path;
    }
    for (const r of map.regions) {
      let [x, y] = labelPoint(r.geometry, proj.fn);
      if (r.callout) {
        // tiny region: clickable box off the coast, joined to the region by a leader line
        const [bx, by] = proj.fn(r.callout);
        const line = document.createElementNS(svgNS, 'line');
        Object.entries({ x1: x, y1: y, x2: bx - 16, y2: by, class: 'leader' }).forEach(([k, v]) => line.setAttribute(k, v));
        const box = document.createElementNS(svgNS, 'rect');
        Object.entries({ x: bx - 16, y: by - 9, width: 44, height: 18, rx: 3, class: 'callout' }).forEach(([k, v]) => box.setAttribute(k, v));
        box.addEventListener('click', () => onSelect(r.id));
        box.addEventListener('mousemove', e => tooltip(e, r.id));
        box.addEventListener('mouseleave', () => tooltip(null));
        this.svg.append(line, box);
        this.boxes[r.id] = box;
        [x, y] = [bx + 6, by];
      }
      const t = document.createElementNS(svgNS, 'text');
      t.setAttribute('x', x); t.setAttribute('y', y + 3);
      t.textContent = r.abbr ? `${r.abbr} ${r.seats}` : r.seats;
      this.svg.appendChild(t);
      this.labels[r.id] = t;
    }
    container.appendChild(this.svg);
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
