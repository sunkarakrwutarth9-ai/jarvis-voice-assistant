// =================== THE ORB: a living sphere of dots that morphs into 24 styles (three.js, GPU) ===================
import * as THREE from "/vendor/three.module.min.js";
const canvas = document.getElementById("gl");
const renderer = new THREE.WebGLRenderer({canvas, antialias: false, alpha: false, powerPreference: "high-performance"});
renderer.setPixelRatio(Math.min(1, 1600 / innerWidth) * Math.min(devicePixelRatio, 1)); // cap the pixel count (4K + integrated GPUs)
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x03040a);
const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100); camera.position.set(0, 0, 7.2);
function resize() { const w = innerWidth, h = innerHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); }
addEventListener("resize", resize); resize();

// ---------------------------------------------------------------- settings (remembered in this browser)
const DEFAULTS = {style: "ultron", dots: 3200, spin: 1, cycle: false, sats: false};
let SET = Object.assign({}, DEFAULTS);
try { Object.assign(SET, JSON.parse(localStorage.getItem("atomoOrb") || "{}")); } catch (e) {}
const saveSet = () => { try { localStorage.setItem("atomoOrb", JSON.stringify(SET)); } catch (e) {} };

// ---------------------------------------------------------------- shapes: each fills N points (x,y,z) ~ radius 1.6
const R = Math.random, TAU = Math.PI * 2;
const SHAPES = {
  sphere: (i, n) => { const y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), t = i * 2.399963; return [Math.cos(t) * r * 1.6, y * 1.6, Math.sin(t) * r * 1.6]; },
  galaxy: (i) => { const arm = i % 3, d = Math.pow(R(), .7) * 2.1 + .1, a = d * 2.4 + arm * TAU / 3 + (R() - .5) * .5;
    return [Math.cos(a) * d, (R() - .5) * .25 * (2.3 - d), Math.sin(a) * d]; },
  torus: () => { const u = R() * TAU, v = R() * TAU; return [(1.25 + .45 * Math.cos(v)) * Math.cos(u), .45 * Math.sin(v), (1.25 + .45 * Math.cos(v)) * Math.sin(u)]; },
  helix: (i, n) => { const t = i / n, y = (t - .5) * 3.4, a = t * TAU * 3.5, s = i % 7 === 0;
    if (s) { const k = R(); return [Math.cos(a) * .75 * (2 * k - 1), y, Math.sin(a) * .75 * (2 * k - 1)]; }
    const b = a + (i % 2) * Math.PI; return [Math.cos(b) * .75, y, Math.sin(b) * .75]; },
  cube: () => { const f = Math.floor(R() * 6), a = R() * 2.2 - 1.1, b = R() * 2.2 - 1.1, s = f % 2 ? 1.1 : -1.1;
    return f < 2 ? [s, a, b] : f < 4 ? [a, s, b] : [a, b, s]; },
  heart: () => { const t = R() * TAU, k = Math.sqrt(R()); const x = 16 * Math.sin(t) ** 3, y = 13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t);
    return [x * .09 * k, y * .09 * k + .2, (R() - .5) * .9 * k * (1 - Math.abs(y) / 40)]; },
  wave: (i, n) => { const s = Math.ceil(Math.sqrt(n)), x = (i % s) / s * 4 - 2, z = Math.floor(i / s) / s * 4 - 2;
    return [x, Math.sin(x * 2) * Math.cos(z * 2) * .35, z]; },
  saturn: (i) => { if (i % 20 < 11) return SHAPES.sphere(Math.floor(R() * 4000), 4000).map(v => v * .62);
    const a = R() * TAU, d = 1.35 + R() * .8; return [Math.cos(a) * d, Math.sin(a) * d * .18, Math.sin(a) * d * .98]; },
  knot: () => { const t = R() * TAU, p = 2, q = 3, r = .55 * (2 + Math.cos(q * t)); const j = () => (R() - .5) * .14;
    return [r * Math.cos(p * t) + j(), .55 * Math.sin(q * t) * 1.2 + j(), r * Math.sin(p * t) + j()]; },
  vortex: () => { const y = R() * 3.4 - 1.7, r = .15 + (y + 1.7) / 3.4 * 1.15, a = y * 5 + R() * TAU; return [Math.cos(a) * r, y, Math.sin(a) * r]; },
  flower: (i, n) => { const [x, y, z] = SHAPES.sphere(i, n), th = Math.atan2(z, x), ph = Math.acos(y / 1.6), k = 1 + .32 * Math.cos(6 * th) * Math.sin(ph);
    return [x * k * .85, y * k * .85, z * k * .85]; },
  shell: (i, n) => { const t = i / n * 7 * Math.PI, g = .06 * Math.exp(.13 * t), a = R() * TAU;
    return [Math.cos(t) * g * 2.2 + Math.cos(a) * g * .9, (t / (7 * Math.PI) - .5) * 2.6 + Math.sin(a) * g * .9, Math.sin(t) * g * 2.2]; },
  coil: (i, n) => { const t = i / n * TAU * 7, a = R() * TAU; const cx = Math.cos(t) * 1.1, cz = Math.sin(t) * 1.1;
    return [cx + Math.cos(a) * .12 * Math.cos(t), i / n * 3.2 - 1.6 + Math.sin(a) * .12, cz + Math.cos(a) * .12 * Math.sin(t)]; },
  diamond: () => { let v = [R() - .5, R() - .5, R() - .5]; const s = Math.abs(v[0]) + Math.abs(v[1]) * .8 + Math.abs(v[2]) || 1; return v.map(c => c / s * 1.7); },
  infinity: () => { const t = R() * TAU, d = 1 + Math.sin(t) ** 2, a = R() * TAU, r = .16;
    return [2 * Math.cos(t) / d + Math.cos(a) * r, 2 * Math.sin(t) * Math.cos(t) / d + Math.sin(a) * r, Math.sin(a) * r * 1.4]; },
  mobius: () => { const u = R() * TAU, v = (R() - .5) * .9; const k = 1.3 + v * Math.cos(u / 2);
    return [k * Math.cos(u), v * Math.sin(u / 2) * 1.3, k * Math.sin(u)]; },
  spiky: (i, n) => { const [x, y, z] = SHAPES.sphere(i, n), th = Math.atan2(z, x), ph = Math.acos(y / 1.6);
    const k = .75 + .45 * Math.pow(Math.abs(Math.sin(5 * th) * Math.sin(5 * ph)), 3); return [x * k, y * k, z * k]; },
  cloud: () => { const g = () => (R() + R() + R() - 1.5) * 1.05; return [g(), g() * .8, g()]; },
  crown: () => { const a = R() * TAU, h = R(), top = .5 + .55 * Math.abs(Math.sin(5 * a)); return [Math.cos(a) * 1.35, (h * top) * 2 - .55, Math.sin(a) * 1.35]; },
  gyro: (i) => { const ring = i % 5, a = R() * TAU, r = 1.65 - ring * .12, tilt = ring * Math.PI / 5;
    const x = Math.cos(a) * r, y = Math.sin(a) * r; return [x, y * Math.cos(tilt), y * Math.sin(tilt)]; },
};
const PAL = {ultron: [0xff1a2e, 0xff8a3d], arc: [0x7fe6ff, 0xf4ba42], iron: [0xe0262f, 0xf4ba42], matrix: [0x00ff6a, 0x0a8f3c], sunset: [0xff7a59, 0xff3d9a],
  ocean: [0x2fd4ff, 0x1f5bff], violet: [0x9b7bff, 0xff6fd8], ice: [0xffffff, 0x7fe6ff], fire: [0xff3b00, 0xffc400],
  gold: [0xf4ba42, 0xfff1c2], mono: [0xffffff, 0x8a94a8], rainbow: [0xffffff, 0xffffff]};
const STYLES = [
  ["ultron", "Ultron Core", "sphere", "ultron"], ["arc-sphere", "Arc Sphere", "sphere", "arc"], ["iron-sphere", "Iron Sphere", "sphere", "iron"],
  ["galaxy", "Galaxy", "galaxy", "violet"], ["andromeda", "Andromeda", "galaxy", "ice"],
  ["torus", "Torus", "torus", "ocean"], ["dna", "DNA Helix", "helix", "matrix"], ["cube", "Data Cube", "cube", "arc"],
  ["heart", "Heart", "heart", "sunset"], ["wave", "Ocean Wave", "wave", "ocean"], ["saturn", "Saturn", "saturn", "gold"],
  ["knot", "Quantum Knot", "knot", "violet"], ["vortex", "Vortex", "vortex", "fire"], ["flower", "Lotus", "flower", "sunset"],
  ["shell", "Nautilus", "shell", "ice"], ["coil", "Coil", "coil", "matrix"], ["diamond", "Diamond", "diamond", "ice"],
  ["infinity", "Infinity", "infinity", "rainbow"], ["mobius", "Möbius", "mobius", "arc"], ["spiky", "Solar Flare", "spiky", "fire"],
  ["nebula", "Nebula", "cloud", "violet"], ["crown", "Crown", "crown", "iron"], ["gyro", "Gyroscope", "gyro", "gold"],
  ["rainbow", "Rainbow Sphere", "sphere", "rainbow"], ["matrix", "Matrix Sphere", "sphere", "matrix"], ["mono", "Moonlight", "sphere", "mono"]];
const STYLE = Object.fromEntries(STYLES.map(s => [s[0], s]));

// ---------------------------------------------------------------- the points
const pivot = new THREE.Group(); scene.add(pivot);
const orb = new THREE.Group(); pivot.add(orb);
const HAND = {rx: 0, ry: 0, zoom: 1, burst: 1, rush: 0, active: 0};
const uniforms = {uTime: {value: 0}, uLevel: {value: 0}, uChaos: {value: .12}, uA: {value: new THREE.Color(0x7fe6ff)},
  uB: {value: new THREE.Color(0xf4ba42)}, uSize: {value: 2.6 * renderer.getPixelRatio()}, uRainbow: {value: 0}, uBurst: {value: 1}};
const mat = new THREE.ShaderMaterial({uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
  vertexShader: `
    uniform float uTime, uLevel, uChaos, uSize, uBurst; attribute float seed; varying float vMix; varying float vAlpha; varying vec3 vP;
    float n3(vec3 p){ return sin(p.x*2.1+uTime*1.3)*sin(p.y*1.9-uTime*1.1)*sin(p.z*2.3+uTime*0.9); }
    void main(){
      vec3 p = position;
      float n = n3(p*1.1 + seed);
      p *= (1.0 + n*(uChaos + uLevel*0.5)*0.35 + uLevel*0.16) * uBurst;
      p += 0.012*vec3(sin(uTime*2.0+seed*40.0), cos(uTime*1.7+seed*30.0), sin(uTime*1.3+seed*20.0));
      vec4 mv = modelViewMatrix * vec4(p,1.0);
      gl_Position = projectionMatrix * mv;
      gl_PointSize = uSize * (0.85 + seed*0.4 + uLevel*0.8) * (6.0 / -mv.z);
      vMix = clamp(0.5 + n*0.8 + p.y*0.18, 0.0, 1.0);
      vAlpha = (0.45 + 0.4*seed) * (1.0 + uLevel*0.8);
      vP = position;
    }`,
  fragmentShader: `
    uniform vec3 uA, uB; uniform float uRainbow, uTime; varying float vMix; varying float vAlpha; varying vec3 vP;
    vec3 hsv(float h){ vec3 k = clamp(abs(mod(h*6.0+vec3(0.,4.,2.),6.)-3.)-1., 0., 1.); return k; }
    void main(){ vec2 c = gl_PointCoord - 0.5; float d = length(c); if(d>0.5) discard;
      vec3 col = mix(uA, uB, vMix);
      col = mix(col, hsv(fract(atan(vP.z, vP.x)/6.2831 + vP.y*0.12 + uTime*0.05)), uRainbow);
      gl_FragColor = vec4(col, smoothstep(0.5, 0.05, d) * vAlpha); }`});
let geo = null, points = null, morph = null;
function shapePositions(shape, n) {
  const f = SHAPES[shape] || SHAPES.sphere, out = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) out.set(f(i, n), i * 3);
  return out;
}
function build(n) {
  if (points) { orb.remove(points); geo.dispose(); }
  geo = new THREE.BufferGeometry();
  const seed = new Float32Array(n); for (let i = 0; i < n; i++) seed[i] = R();
  geo.setAttribute("position", new THREE.BufferAttribute(shapePositions(STYLE[SET.style]?.[2] || "sphere", n), 3));
  geo.setAttribute("seed", new THREE.BufferAttribute(seed, 1));
  points = new THREE.Points(geo, mat); orb.add(points);
}
build(SET.dots);
let pal = PAL[STYLE[SET.style]?.[3] || "arc"], rainbow = (STYLE[SET.style]?.[3] === "rainbow") ? 1 : 0;
function setStyle(id, quiet) {
  const st = STYLE[id] || STYLES.find(s => s[1].toLowerCase().includes(String(id).toLowerCase())) || null;
  if (!st) return false;
  SET.style = st[0]; saveSet();
  const pos = geo.attributes.position, from = pos.array.slice(), to = shapePositions(st[2], pos.count);
  morph = {from, to, t0: performance.now()};
  pal = PAL[st[3]]; rainbow = st[3] === "rainbow" ? 1 : 0;
  if (!quiet && window.toast) window.toast("✦ " + st[1]);
  renderSettings();
  return true;
}

// soft glow at the heart + stars
function glowTex(c1, c2) { const cv = document.createElement("canvas"); cv.width = cv.height = 128; const x = cv.getContext("2d");
  const g = x.createRadialGradient(64, 64, 0, 64, 64, 64); g.addColorStop(0, c1); g.addColorStop(.3, c2); g.addColorStop(1, "rgba(0,0,0,0)");
  x.fillStyle = g; x.fillRect(0, 0, 128, 128); return new THREE.CanvasTexture(cv); }
const core = new THREE.Sprite(new THREE.SpriteMaterial({map: glowTex("rgba(255,255,255,.9)", "rgba(127,230,255,.35)"), blending: THREE.AdditiveBlending, depthWrite: false}));
core.scale.set(1.2, 1.2, 1); orb.add(core);
const S = 700, sp = new Float32Array(S * 3);
for (let i = 0; i < S; i++) { const r = 18 + R() * 25, t = R() * TAU, f = Math.acos(2 * R() - 1);
  sp.set([r * Math.sin(f) * Math.cos(t), r * Math.sin(f) * Math.sin(t), r * Math.cos(f)], i * 3); }
const sg = new THREE.BufferGeometry(); sg.setAttribute("position", new THREE.BufferAttribute(sp, 3));
const stars = new THREE.Points(sg, new THREE.PointsMaterial({color: 0x9fb4ff, size: .06, transparent: true, opacity: .5}));
scene.add(stars);

// optional: your heaviest apps orbit as labelled moons (off by default - Settings)
const satTex = glowTex("rgba(255,255,255,1)", "rgba(155,123,255,.6)"), _sv = new THREE.Vector3(), sats = [];
for (let i = 0; i < 5; i++) {
  const s = new THREE.Sprite(new THREE.SpriteMaterial({map: satTex, color: 0xb9a4ff, blending: THREE.AdditiveBlending, depthWrite: false}));
  s.visible = false; orb.add(s);
  const lab = document.createElement("div"); lab.className = "sat"; document.getElementById("sats").appendChild(lab);
  sats.push({s, lab, a: i * 1.2566, r: 2.6 + (i % 2) * .35, tilt: (i - 2) * .16});
}

// ---------------------------------------------------------------- settings panel
const css = document.createElement("style");
css.textContent = `.oset{position:fixed;inset:0;z-index:85;display:none;align-items:center;justify-content:center;background:rgba(2,4,10,.6)}
.oset.show{display:flex}.obox{width:min(860px,94vw);max-height:86vh;overflow:auto;border-radius:20px;background:rgba(10,14,26,.97);
border:1px solid rgba(127,230,255,.3);box-shadow:0 30px 90px rgba(0,0,0,.6);padding:20px 22px;animation:pop .2s}
.obox h3{font:600 13px var(--hud);letter-spacing:5px;color:var(--arc);margin:4px 0 12px;display:flex;align-items:center}
.obox h3 button{margin-left:auto;border:0;background:none;color:var(--sub);font-size:18px;cursor:pointer}
.ogrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(122px,1fr));gap:10px}
.ost{border:1px solid var(--stroke);border-radius:14px;padding:10px;cursor:pointer;background:rgba(255,255,255,.03);text-align:center;font-size:12px}
.ost.on{border-color:var(--gold);box-shadow:0 0 18px rgba(244,186,66,.25)}.ost:hover{border-color:var(--arc)}
.ost i{display:block;width:46px;height:46px;margin:2px auto 7px;border-radius:50%}
.orow{display:flex;align-items:center;gap:14px;margin:12px 0;font-size:13px;flex-wrap:wrap}.orow span{width:150px;color:var(--sub)}
.orow button{border:1px solid var(--stroke);background:var(--glass);color:var(--text);border-radius:9px;padding:6px 12px;font:600 12px var(--font);cursor:pointer}
.orow button.on{background:var(--accent-grad);border:0;color:#fff}.orow input[type=range]{width:220px}`;
document.head.appendChild(css);
const box = document.createElement("div"); box.className = "oset"; box.id = "oset"; document.body.appendChild(box);
const hex = c => "#" + c.toString(16).padStart(6, "0");
function renderSettings() {
  const sw = s => s[3] === "rainbow" ? "conic-gradient(#ff4d5a,#f4ba42,#3ee08b,#7fe6ff,#9b7bff,#ff4d5a)"
    : `radial-gradient(circle at 35% 30%,#fff 0 6%,${hex(PAL[s[3]][0])} 22%,${hex(PAL[s[3]][1])} 70%,transparent 72%)`;
  box.innerHTML = `<div class="obox"><h3>⚙ ORB SETTINGS — ${STYLES.length} STYLES<button id="osX">✕</button></h3>
    <div class="ogrid">${STYLES.map(s => `<div class="ost ${s[0] === SET.style ? "on" : ""}" data-s="${s[0]}"><i style="background:${sw(s)}"></i>${s[1]}</div>`).join("")}</div>
    <div class="orow"><span>Dots</span>${[["Light", 1800], ["Balanced", 3200], ["Dense", 5200]].map(([l, n]) => `<button data-d="${n}" class="${SET.dots === n ? "on" : ""}">${l}</button>`).join("")}</div>
    <div class="orow"><span>Spin speed</span><input type="range" id="osSpin" min="0" max="3" step="0.1" value="${SET.spin}"></div>
    <div class="orow"><span>Auto-change style</span><button id="osCycle" class="${SET.cycle ? "on" : ""}">${SET.cycle ? "Every minute" : "Off"}</button></div>
    <div class="orow"><span>App satellites</span><button id="osSats" class="${SET.sats ? "on" : ""}">${SET.sats ? "On" : "Off"}</button></div>
    <div class="orow" style="color:var(--sub)">Tip: say “change the orb to galaxy”. Your hand can turn and zoom it when gestures are on.</div></div>`;
  box.querySelectorAll("[data-s]").forEach(el => el.onclick = () => setStyle(el.dataset.s));
  box.querySelectorAll("[data-d]").forEach(el => el.onclick = () => { SET.dots = +el.dataset.d; saveSet(); build(SET.dots); renderSettings(); });
  box.querySelector("#osSpin").oninput = e => { SET.spin = +e.target.value; saveSet(); };
  box.querySelector("#osCycle").onclick = () => { SET.cycle = !SET.cycle; saveSet(); renderSettings(); };
  box.querySelector("#osSats").onclick = () => { SET.sats = !SET.sats; saveSet(); renderSettings(); };
  box.querySelector("#osX").onclick = () => box.classList.remove("show");
}
renderSettings();
box.onclick = e => { if (e.target === box) box.classList.remove("show"); };
addEventListener("keydown", e => { if (e.key === "Escape") box.classList.remove("show"); });
window.ORB.openSettings = () => { renderSettings(); box.classList.add("show"); };
window.ORB.setTheme = t => {
  pal = [parseInt(t.a1.slice(1), 16), parseInt(t.a2.slice(1), 16)]; rainbow = 0;
  scene.background = new THREE.Color(t.bg);
  mat.blending = t.light ? THREE.NormalBlending : THREE.AdditiveBlending; mat.needsUpdate = true;
  stars.visible = !t.light; core.visible = !t.light;
};
window.ORB.setStyle = setStyle;
window.ORB.styles = STYLES.map(s => s[1]);
if (window.ORB.pendingStyle) setStyle(window.ORB.pendingStyle, true);
setInterval(() => { if (SET.cycle && !document.hidden) { const i = STYLES.findIndex(s => s[0] === SET.style); setStyle(STYLES[(i + 1) % STYLES.length][0]); } }, 60000);

// ---------------------------------------------------------------- animation
const STATE_COL = {listening: [0xff3b45, 0xf4ba42], thinking: [0x9b7bff, 0x7fe6ff], action: [0x9b7bff, 0xf4ba42],
                   speaking: [0xf4ba42, 0xff7a2f], error: [0xff2d2d, 0xff7a2f]};
const SPIN = {idle: .08, followup: .15, listening: .25, thinking: 1.2, action: .8, speaking: .3, error: .1, choice: .2};
const cA = new THREE.Color(), cB = new THREE.Color(), tA = new THREE.Color(), tB = new THREE.Color();
let level = 0, spin = .08, t0 = performance.now(), scale = 1, yOff = 0, last = performance.now();
function frame(now) {
  requestAnimationFrame(frame);
  if (document.hidden) return;
  const t = (now - t0) / 1000, dt = Math.min(.05, (now - last) / 1000), st = window.ORB.state; last = now;
  level += ((window.ORB.level || 0) - level) * .15;
  spin += ((SPIN[st] ?? .1) - spin) * .04;
  // colours: the style's palette, tinted by what Ultron is doing
  tA.set(pal[0]); tB.set(pal[1]);
  if (STATE_COL[st]) { tA.lerp(cA.set(STATE_COL[st][0]), .6); tB.lerp(cB.set(STATE_COL[st][1]), .6); }
  uniforms.uA.value.lerp(tA, .06); uniforms.uB.value.lerp(tB, .06);
  uniforms.uRainbow.value += ((STATE_COL[st] ? rainbow * .3 : rainbow) - uniforms.uRainbow.value) * .05;
  uniforms.uTime.value = t;
  uniforms.uChaos.value += ((st === "thinking" ? .35 : .1) - uniforms.uChaos.value) * .05;
  // morph between styles
  if (morph) {
    const k = Math.min(1, (now - morph.t0) / 1300), e = k < .5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
    const a = geo.attributes.position.array, f = morph.from, to = morph.to;
    for (let i = 0; i < a.length; i++) a[i] = f[i] + (to[i] - f[i]) * e;
    geo.attributes.position.needsUpdate = true;
    if (k >= 1) morph = null;
  }
  // hand control (gesture engine): palm turns it, distance zooms, fist collapses, open hand bursts, ✌️ spins fast
  const hd = window.ORB.hand, live = hd && performance.now() - hd.t < 600;
  HAND.active += ((live ? 1 : 0) - HAND.active) * .08;
  let tRx = 0, tRy = 0, tZoom = 1, tBurst = 1, tRush = 0;
  if (live) {
    const P = hd.pts, c = P[9], w = P[0];
    const size = Math.hypot(P[5][0] - P[17][0], P[5][1] - P[17][1]) + Math.hypot(c[0] - w[0], c[1] - w[1]);
    tRy = (0.5 - c[0]) * Math.PI * 1.6; tRx = (c[1] - 0.5) * Math.PI * 0.9;
    tZoom = Math.min(1.6, Math.max(0.6, size / 0.32));
    if (hd.g === "fist") tBurst = 0.35; else if (hd.g === "palm") tBurst = 1.35;
    if (hd.g === "victory") tRush = 3;
    if (hd.pinch) tZoom *= 0.75;
  }
  HAND.rx += (tRx - HAND.rx) * .12; HAND.ry += (tRy - HAND.ry) * .12; HAND.zoom += (tZoom - HAND.zoom) * .1;
  HAND.burst += (tBurst - HAND.burst) * .1; HAND.rush += (tRush - HAND.rush) * .06;
  uniforms.uBurst.value = HAND.burst;
  uniforms.uLevel.value = Math.max(level, (HAND.burst - 1) * .8);
  pivot.rotation.set(HAND.rx, HAND.ry, 0);
  orb.rotation.y += (spin + HAND.rush) * .016 * SET.spin * (1 - HAND.active * .6);
  orb.rotation.x = Math.sin(t * .3) * .14 * (1 - HAND.active);
  core.material.color.copy(uniforms.uA.value); core.scale.setScalar(1.0 + level * 1.4 + Math.sin(t * 2) * .04);
  stars.rotation.y += .0003;
  // satellites (optional)
  const P = window.ORB.procs || [], showSats = SET.sats && !window.ORB.canvas && !document.body.classList.contains("amb");
  sats.forEach((q, i) => {
    const p = P[i];
    if (!p || !showSats) { if (q.s.visible) { q.s.visible = false; q.lab.style.display = "none"; } return; }
    q.a += dt * (.22 - i * .025);
    q.s.position.set(Math.cos(q.a) * q.r, Math.sin(q.a * 1.3) * q.r * q.tilt, Math.sin(q.a) * q.r);
    const sc = .22 + Math.min(p.gb, 4) * .1; q.s.scale.set(sc, sc, 1); q.s.visible = true;
    _sv.setFromMatrixPosition(q.s.matrixWorld).project(camera);
    if (_sv.z < 1) { q.lab.style.display = "block";
      q.lab.style.transform = `translate(${((_sv.x * .5 + .5) * innerWidth).toFixed(0)}px,${((-_sv.y * .5 + .5) * innerHeight).toFixed(0)}px)`;
      const txt = p.name.replace(/\.exe$/i, "") + " · " + p.gb + " GB"; if (q.lab.textContent !== txt) q.lab.textContent = txt; }
    else q.lab.style.display = "none";
  });
  // canvas open: the orb shrinks and rises above the work area
  const tScale = window.ORB.canvas ? .32 : 1, tY = window.ORB.canvas ? 2.35 : 0.25;
  scale += (tScale - scale) * .06; yOff += (tY - yOff) * .06;
  orb.scale.setScalar(scale * HAND.zoom); orb.position.y = yOff;
  renderer.render(scene, camera);
}
requestAnimationFrame(frame);
