// ULTRON UI themes: 200 looks in 10 categories, shared by every page (command center, Study Mode, Ultron
// Screen, phone remote). A theme sets colours, fonts, background pattern, panel style and corner radius;
// each page maps those tokens onto its own CSS variables. Pick one in the gallery (ULTRON_THEME.openGallery()).
(function () {
  const C = (name, a1, a2, a3) => [name, a1, a2, a3 || a2];
  const CATS = [
    {id: "apple", name: "Apple Clean", icon: "", font: "Inter", display: "Inter", radius: 22, pattern: "none", panel: "glass",
     list: [C("Blue", "#0a84ff", "#5e5ce6"), C("Purple", "#bf5af2", "#5e5ce6"), C("Pink", "#ff375f", "#ff9f0a"), C("Red", "#ff453a", "#ff9f0a"),
            C("Orange", "#ff9f0a", "#ffd60a"), C("Green", "#30d158", "#64d2ff"), C("Mint", "#66d4cf", "#30d158"), C("Teal", "#40c8e0", "#0a84ff"),
            C("Indigo", "#5e5ce6", "#bf5af2"), C("Graphite", "#8e8e93", "#0a84ff"), C("Blue Light", "#007aff", "#5856d6"), C("Purple Light", "#af52de", "#5856d6"),
            C("Pink Light", "#ff2d55", "#ff9500"), C("Red Light", "#ff3b30", "#ff9500"), C("Orange Light", "#ff9500", "#ffcc00"), C("Green Light", "#34c759", "#5ac8fa"),
            C("Teal Light", "#30b0c7", "#007aff"), C("Indigo Light", "#5856d6", "#af52de"), C("Graphite Light", "#636366", "#007aff"), C("Sky Light", "#32ade6", "#34c759")]},
    {id: "ironman", name: "Iron Man HUD", icon: "🦾", font: "Rajdhani", display: "Orbitron", radius: 6, pattern: "hud", panel: "hud",
     list: [C("Mark III", "#e0262f", "#f4ba42", "#7fe6ff"), C("Arc Reactor", "#7fe6ff", "#e8fbff", "#2fa8ff"), C("Mark 42", "#d81f26", "#ffc94a"), C("Mark 50 Nano", "#b3121b", "#ffb000", "#4af0ff"),
            C("War Machine", "#9aa4b1", "#e6e9ee", "#ff3b30"), C("Iron Patriot", "#d7263d", "#f2f4f8", "#1b5fd8"), C("Stealth", "#5d6b7a", "#c9d4e0", "#7fe6ff"), C("Hulkbuster", "#ff4d2e", "#ffd166"),
            C("Rescue", "#4a6cff", "#a78bfa", "#7fe6ff"), C("Mark 85", "#c1121f", "#fdc500", "#89f7fe"), C("JARVIS Blue", "#00d4ff", "#0077ff"), C("FRIDAY", "#ff8c42", "#ffd166", "#7fe6ff"),
            C("Ultron Red", "#ff1a2e", "#ff8a3d"), C("Vision", "#ffc300", "#2ecc71", "#ff3864"), C("EDITH", "#00ffa3", "#00b3ff"), C("Stark Expo", "#ff2e63", "#08d9d6"),
            C("Gold Titanium", "#f4ba42", "#fff1c2", "#e0262f"), C("Mark 7", "#e63946", "#f1faee", "#a8dadc"), C("Bleeding Edge", "#ff0054", "#ffbd00"), C("Holo Lab", "#3df5ff", "#9b7bff", "#f4ba42")]},
    {id: "neon", name: "Neon Cyberpunk", icon: "🌃", font: "Exo 2", display: "Audiowide", radius: 14, pattern: "grid", panel: "neon",
     list: [C("Night City", "#ff2a6d", "#05d9e8", "#d1f7ff"), C("Magenta Rush", "#ff00c8", "#00f0ff"), C("Toxic", "#39ff14", "#00e5ff"), C("Laser Lemon", "#fffb00", "#ff00e1"),
            C("Plasma", "#b026ff", "#ff2fd6"), C("Ice Neon", "#00f0ff", "#7b61ff"), C("Hot Pink", "#ff1493", "#ffd319"), C("Electric Lime", "#c6ff00", "#00e676"),
            C("Synth Violet", "#8a2be2", "#00bfff"), C("Blade Runner", "#ff6b35", "#00c2d1"), C("Glitch", "#00ff9f", "#ff003c"), C("Ultraviolet", "#7f00ff", "#e100ff"),
            C("Tokyo Rain", "#00b4d8", "#f72585"), C("Cyber Gold", "#ffd60a", "#00f5d4"), C("Red Alert", "#ff073a", "#ffb703"), C("Aqua Pulse", "#00fff0", "#00ff85"),
            C("Mainframe", "#00ff41", "#008f11"), C("Vapor Pink", "#ff71ce", "#01cdfe", "#b967ff"), C("Neon Orange", "#ff6700", "#00d9ff"), C("Hologram", "#a0f", "#0ff", "#f0f")]},
    {id: "glass", name: "Glassmorphism", icon: "🫧", font: "Poppins", display: "Poppins", radius: 26, pattern: "blobs", panel: "frost",
     list: [C("Aurora Glass", "#7f5af0", "#2cb67d"), C("Peach Glass", "#ff8fab", "#ffb703"), C("Ocean Glass", "#00b4d8", "#0077b6"), C("Lilac Glass", "#c77dff", "#7b2cbf"),
            C("Citrus Glass", "#f9c74f", "#f3722c"), C("Mint Glass", "#52b788", "#b7e4c7"), C("Berry Glass", "#d00000", "#9d0208", "#ffba08"), C("Sky Glass", "#4cc9f0", "#4361ee"),
            C("Rose Glass", "#ff4d6d", "#ffb3c1"), C("Cosmic Glass", "#7209b7", "#f72585"), C("Lagoon Glass", "#06d6a0", "#118ab2"), C("Amber Glass", "#ff9f1c", "#ffbf69"),
            C("Frost Glass", "#a2d2ff", "#bde0fe"), C("Grape Glass", "#6a4c93", "#c77dff"), C("Coral Glass", "#ff6b6b", "#feca57"), C("Jade Glass", "#00a878", "#d8f1a0"),
            C("Indigo Glass", "#3a0ca3", "#4895ef"), C("Candy Glass", "#ff70a6", "#70d6ff"), C("Sunset Glass", "#ff5e5b", "#ffed66"), C("Pearl Glass", "#cdb4db", "#a2d2ff")]},
    {id: "space", name: "Space & Cosmos", icon: "🌌", font: "Space Grotesk", display: "Orbitron", radius: 18, pattern: "stars", panel: "glass",
     list: [C("Andromeda", "#7b61ff", "#ff6ad5"), C("Orion Nebula", "#ff6f91", "#845ec2"), C("Milky Way", "#cdb4db", "#ffc8dd"), C("Mars Base", "#e4572e", "#f3a712"),
            C("Saturn Rings", "#f4d35e", "#ee964b"), C("Neptune", "#3a86ff", "#00f5d4"), C("Event Horizon", "#ff7b00", "#ffd000"), C("Supernova", "#ff006e", "#fb5607", "#ffbe0b"),
            C("Pillars", "#c9184a", "#ff8fa3"), C("Pulsar", "#00e5ff", "#ffffff"), C("Jupiter Storm", "#d4a373", "#e76f51"), C("Moonlight", "#e0e1dd", "#778da9"),
            C("Eclipse", "#ffb703", "#219ebc"), C("Quasar", "#9d4edd", "#e0aaff"), C("Comet", "#48cae4", "#90e0ef"), C("Red Dwarf", "#d00000", "#ff7b00"),
            C("Galaxy Core", "#ffd6a5", "#ffadad"), C("Deep Field", "#4361ee", "#7209b7"), C("Aurora Borealis", "#06ffa5", "#7b2cbf"), C("Stardust", "#f1c0e8", "#cfbaf0")]},
    {id: "nature", name: "Nature", icon: "🌿", font: "Nunito", display: "Comfortaa", radius: 24, pattern: "waves", panel: "soft",
     list: [C("Forest", "#2d6a4f", "#95d5b2"), C("Ocean", "#0077b6", "#90e0ef"), C("Desert", "#e09f3e", "#9e2a2b"), C("Sakura", "#ff8fab", "#ffc2d1"),
            C("Autumn", "#bc4b51", "#f4a259"), C("Lavender Field", "#9d4edd", "#e0aaff"), C("Tea Garden", "#588157", "#a3b18a"), C("Monsoon", "#4a6fa5", "#9db4c0"),
            C("Sunflower", "#ffb703", "#fb8500"), C("Coral Reef", "#ff6f59", "#43aa8b"), C("Glacier", "#a8dadc", "#457b9d"), C("Rainforest", "#1b4332", "#52b788"),
            C("Lotus Pond", "#e5989b", "#6d6875"), C("Mountain Dawn", "#f6bd60", "#84a59d"), C("Meadow", "#80b918", "#d4d700"), C("Canyon", "#bb3e03", "#ee9b00"),
            C("Moss", "#606c38", "#dda15e"), C("Beach", "#00b4d8", "#ffd166"), C("Bamboo", "#6a994e", "#a7c957"), C("Night Forest", "#40916c", "#b7e4c7")]},
    {id: "retro", name: "Retro & Synthwave", icon: "📼", font: "VT323", display: "Press Start 2P", radius: 4, pattern: "scan", panel: "crt",
     list: [C("Outrun", "#ff2975", "#f222ff", "#8c1eff"), C("Vaporwave", "#ff71ce", "#01cdfe", "#05ffa1"), C("Arcade", "#ffde00", "#ff006e"), C("Green CRT", "#33ff33", "#00aa00"),
            C("Amber CRT", "#ffb000", "#ff7700"), C("80s Miami", "#ff6ec7", "#00d4ff"), C("Game Boy", "#9bbc0f", "#8bac0f", "#306230"), C("Commodore", "#7869c4", "#a5a0ff"),
            C("Tron", "#00e5ff", "#ff9e00"), C("Retro Sunset", "#ff6b35", "#f7c59f", "#9b5de5"), C("Disco", "#ffd700", "#ff00ff"), C("Cassette", "#ff595e", "#ffca3a"),
            C("Pixel Blue", "#3a86ff", "#ffbe0b"), C("Neon Diner", "#ff3c38", "#2ec4b6"), C("Polaroid", "#f4a261", "#e9c46a"), C("VHS", "#ff4365", "#00d9c0"),
            C("Atari", "#fe5f55", "#f0b67f"), C("Jukebox", "#ef476f", "#ffd166", "#06d6a0"), C("Laser Disc", "#c77dff", "#48cae4"), C("Old Terminal", "#e0e0e0", "#00ff00")]},
    {id: "minimal", name: "Minimal & Simple", icon: "◻️", font: "Inter", display: "Inter", radius: 12, pattern: "none", panel: "flat",
     list: [C("Pure Dark", "#ffffff", "#9e9e9e"), C("Pure Light", "#111111", "#616161"), C("Paper", "#3d405b", "#e07a5f"), C("Sepia", "#8b5e34", "#d4a373"),
            C("High Contrast", "#ffff00", "#00ffff"), C("Ink", "#1d3557", "#e63946"), C("Charcoal", "#e9ecef", "#adb5bd"), C("Snow", "#0d1b2a", "#415a77"),
            C("Slate", "#94a3b8", "#38bdf8"), C("Stone", "#78716c", "#f59e0b"), C("Notebook", "#2b2d42", "#ef233c"), C("Mono Blue", "#1e88e5", "#90caf9"),
            C("Mono Green", "#2e7d32", "#a5d6a7"), C("Mono Red", "#c62828", "#ef9a9a"), C("Calm Gray", "#6c757d", "#adb5bd"), C("Big & Bold", "#ffd60a", "#ffffff"),
            C("Focus", "#4f46e5", "#a5b4fc"), C("Zen", "#84a98c", "#cad2c5"), C("Nordic", "#88c0d0", "#81a1c1"), C("Solar Light", "#b58900", "#268bd2")]},
    {id: "luxury", name: "Luxury", icon: "💎", font: "Montserrat", display: "Playfair Display", radius: 16, pattern: "diamond", panel: "gold",
     list: [C("Black Gold", "#d4af37", "#f9e076"), C("Rose Gold", "#b76e79", "#f4c2c2"), C("Emerald", "#50c878", "#d4af37"), C("Sapphire", "#0f52ba", "#c0c0c0"),
            C("Ruby", "#9b111e", "#d4af37"), C("Platinum", "#e5e4e2", "#a9a9a9"), C("Champagne", "#f7e7ce", "#d4af37"), C("Onyx", "#c0c0c0", "#ffffff"),
            C("Royal Purple", "#7851a9", "#d4af37"), C("Marble", "#bfa181", "#e0d9d0"), C("Velvet", "#7b1e3a", "#e7a977"), C("Pearl", "#eae0c8", "#c9b79c"),
            C("Bronze", "#cd7f32", "#f2c18d"), C("Midnight Gold", "#ffd700", "#1e3a8a"), C("Jade Gold", "#00a86b", "#ffd700"), C("Amethyst", "#9966cc", "#e6cfff"),
            C("Copper", "#b87333", "#e9c29e"), C("Ivory", "#fffff0", "#c2b280"), C("Topaz", "#ffc87c", "#ff9933"), C("Obsidian Blue", "#2e86de", "#d4af37")]},
    {id: "india", name: "Indian Festive", icon: "🪔", font: "Poppins", display: "Kalam", radius: 20, pattern: "mandala", panel: "warm",
     list: [C("Diwali", "#ffb703", "#fb8500", "#e63946"), C("Holi", "#ff006e", "#8338ec", "#3a86ff"), C("Sankranti", "#f77f00", "#fcbf49", "#2a9d8f"), C("Bathukamma", "#ff4d6d", "#ffd60a", "#80ed99"),
            C("Ugadi", "#70e000", "#ffd000"), C("Peacock", "#0f7c80", "#1fbad6", "#2a9d8f"), C("Kanchipuram Silk", "#9b2226", "#ee9b00"), C("Saffron", "#ff9933", "#ffffff", "#138808"),
            C("Lotus", "#e56b6f", "#ffb4a2"), C("Mehendi", "#6b705c", "#cb997e"), C("Rangoli", "#ef476f", "#06d6a0", "#ffd166"), C("Navratri", "#d00000", "#ffba08", "#3f88c5"),
            C("Onam", "#fca311", "#e5e5e5", "#14213d"), C("Pongal", "#e9c46a", "#f4a261"), C("Durga Puja", "#c1121f", "#fdf0d5"), C("Ganesh", "#ff7b00", "#ffd000"),
            C("Taj Marble", "#e7d8c9", "#9a8c98"), C("Monsoon Kerala", "#2d6a4f", "#74c69d"), C("Jaipur Pink", "#e76f8a", "#ffc2d1"), C("Banaras Ghat", "#f4a259", "#5b8e7d")]},
  ];
  const hex = h => { h = h.replace("#", ""); if (h.length === 3) h = h.split("").map(c => c + c).join("");
    return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)]; };
  const mix = (a, b, t) => { const x = hex(a), y = hex(b); return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join(""); };
  const rgba = (h, a) => { const [r, g, b] = hex(h); return `rgba(${r},${g},${b},${a})`; };
  const THEMES = [];
  CATS.forEach(cat => cat.list.forEach(([name, a1, a2, a3], i) => {
    const light = cat.id === "apple" ? i >= 10 : cat.id === "minimal" ? [1, 2, 3, 7, 10, 19].includes(i) :
                  cat.id === "nature" ? [3, 7, 10, 12, 17].includes(i) : cat.id === "luxury" ? [9, 11, 17].includes(i) :
                  cat.id === "india" ? [8, 16, 18].includes(i) : cat.id === "glass" ? [1, 12, 19].includes(i) : false;
    const bg = light ? mix("#ffffff", a1, .06) : mix("#05060b", a1, cat.id === "minimal" ? .02 : .1);
    const bg2 = light ? mix("#ffffff", a2, .14) : mix("#05060b", a2, .22);
    THEMES.push({id: `${cat.id}-${i + 1}`, cat: cat.id, catName: cat.name, name, light, a1, a2, a3, bg, bg2,
      text: light ? "#15161c" : "#eef2ff", sub: light ? "#5c6070" : "#97a0b8",
      panel: cat.panel === "flat" ? (light ? "#ffffff" : mix(bg, "#ffffff", .05)) : light ? "rgba(255,255,255,.72)" : rgba(mix("#0b0e18", a1, .08), cat.panel === "frost" ? .45 : .78),
      line: light ? rgba(a1, .22) : rgba(a1, cat.panel === "hud" || cat.panel === "neon" ? .45 : .2),
      font: cat.font, display: cat.display, radius: cat.radius, pattern: cat.pattern, panelStyle: cat.panel});
  }));
  const byId = Object.fromEntries(THEMES.map(t => [t.id, t]));
  const loaded = new Set();
  function font(f) {
    if (!f || loaded.has(f) || ["Inter", "Segoe UI"].includes(f)) return; loaded.add(f);
    const l = document.createElement("link"); l.rel = "stylesheet";
    l.href = `https://fonts.googleapis.com/css2?family=${encodeURIComponent(f)}:wght@400;600;700&display=swap`; document.head.appendChild(l);
  }
  function pattern(t) {
    const L = rgba(t.a1, t.light ? .08 : .07), L2 = rgba(t.a2, .12);
    return {none: "none",
      hud: `linear-gradient(${L} 1px,transparent 1px),linear-gradient(90deg,${L} 1px,transparent 1px),radial-gradient(circle at 50% 50%,transparent 60%,${rgba(t.a1, .08)} 100%)`,
      grid: `linear-gradient(${rgba(t.a1, .12)} 1px,transparent 1px),linear-gradient(90deg,${rgba(t.a2, .1)} 1px,transparent 1px)`,
      blobs: `radial-gradient(circle at 15% 20%,${rgba(t.a1, .45)},transparent 40%),radial-gradient(circle at 85% 30%,${rgba(t.a2, .4)},transparent 42%),radial-gradient(circle at 50% 90%,${rgba(t.a3, .35)},transparent 45%)`,
      stars: `radial-gradient(1px 1px at 10% 20%,#fff,transparent),radial-gradient(1px 1px at 70% 80%,#fff,transparent),radial-gradient(1.5px 1.5px at 40% 60%,${t.a2},transparent),radial-gradient(1px 1px at 85% 15%,#fff,transparent),radial-gradient(1px 1px at 25% 75%,#fff,transparent),radial-gradient(ellipse at 70% 30%,${rgba(t.a1, .25)},transparent 55%)`,
      waves: `radial-gradient(ellipse 120% 60% at 50% 110%,${rgba(t.a1, .3)},transparent 60%),radial-gradient(ellipse 80% 40% at 0% 0%,${rgba(t.a2, .2)},transparent 60%)`,
      scan: `repeating-linear-gradient(0deg,${rgba("#000000", .25)} 0 1px,transparent 1px 3px),radial-gradient(ellipse at center,${rgba(t.a1, .15)},transparent 70%)`,
      diamond: `linear-gradient(45deg,${L} 25%,transparent 25%,transparent 75%,${L} 75%),linear-gradient(45deg,${L} 25%,transparent 25%,transparent 75%,${L} 75%)`,
      mandala: `repeating-radial-gradient(circle at 50% 50%,${L2} 0 2px,transparent 2px 22px),radial-gradient(circle at 85% 15%,${rgba(t.a1, .3)},transparent 40%),radial-gradient(circle at 10% 90%,${rgba(t.a3, .25)},transparent 40%)`,
    }[t.pattern] || "none";
  }
  // map theme tokens onto each page's own CSS variables
  const MAP = {
    cinema: t => ({"--bg": t.bg, "--glass": t.panel, "--stroke": t.line, "--text": t.text, "--sub": t.sub, "--arc": t.a1, "--gold": t.a2,
                   "--armor": t.a3, "--violet": t.a2, "--accent-grad": `linear-gradient(135deg,${t.a3},${t.a1} 55%,${t.a2})`, "--bubble-j": t.panel,
                   "--font": `"${t.font}","Segoe UI",system-ui,sans-serif`, "--hud": `"${t.display}","${t.font}",sans-serif`}),
    study: t => ({"--bg": t.bg, "--ink": t.text, "--sub": t.sub, "--paper": t.panel, "--line": t.line, "--amber": t.a1, "--rose": t.a3,
                  "--mint": t.a2, "--sky": t.a2, "--violet": t.a3, "--hand": `"${t.display}",cursive`, "--font": `"${t.font}","Segoe UI",system-ui,sans-serif`}),
    screen: t => ({"--bg": t.bg, "--glass": t.panel, "--stroke": t.line, "--text": t.text, "--sub": t.sub, "--arc": t.a1, "--gold": t.a2, "--armor": t.a3,
                   "--accent-grad": `linear-gradient(135deg,${t.a3},${t.a1} 55%,${t.a2})`, "--font": `"${t.font}","Segoe UI",system-ui,sans-serif`, "--hud": `"${t.display}",sans-serif`}),
    phone: t => ({"--arc": t.a1, "--gold": t.a2, "--red": t.a3, "--sub": t.sub, "--card": t.panel, "--line": t.line}),
  };
  let PAGE = "cinema", CURRENT = null;
  function apply(id) {
    const t = byId[id] || byId["ironman-13"]; CURRENT = t.id;
    font(t.font); font(t.display);
    const vars = MAP[PAGE](t), root = document.documentElement;
    Object.entries(vars).forEach(([k, v]) => root.style.setProperty(k, v));
    root.style.setProperty("--radius", t.radius + "px");
    root.dataset.uitheme = t.id; root.dataset.light = t.light ? "1" : "0";
    let st = document.getElementById("uthemeStyle");
    if (!st) { st = document.createElement("style"); st.id = "uthemeStyle"; document.head.appendChild(st); }
    const glow = t.panelStyle === "neon" ? `box-shadow:0 0 0 1px ${rgba(t.a1, .35)},0 0 22px ${rgba(t.a1, .25)}!important;` :
                 t.panelStyle === "hud" ? `box-shadow:inset 0 0 0 1px ${rgba(t.a1, .25)},0 0 30px ${rgba(t.a1, .12)}!important;` :
                 t.panelStyle === "gold" ? `box-shadow:0 0 0 1px ${rgba(t.a1, .35)},0 20px 50px rgba(0,0,0,.5)!important;` : "";
    const pat = pattern(t);
    st.textContent = `html,body{background:${t.light ? `linear-gradient(160deg,${t.bg},${t.bg2})` : `radial-gradient(ellipse at 70% 0%,${t.bg2},${t.bg} 65%)`}!important;color:${t.text}}
      body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;background-image:${pat};${["hud", "grid", "diamond"].includes(t.pattern) ? "background-size:40px 40px;" : ""}}
      .glass,.card,.pwrbox,.palbox,.obox,.mgraph .tip,.chday{border-radius:${t.radius}px!important;${glow}}
      ${t.panelStyle === "frost" ? ".glass,.card{backdrop-filter:blur(22px) saturate(1.4)!important}" : ""}
      ${t.light ? "#gl{opacity:.92}" : ""}
      ${PAGE === "cinema" ? `.glass,.statepill,.hchip,.pwrbtn,.models,.cmdbar,.chips2 button,.dtabs button,.drow,.ccard,.clip,.gtoast,.pal .palbox{background:${t.panel}!important;color:${t.text}}
        .radial button{background:${t.light ? "rgba(255,255,255,.85)" : rgba(mix("#0b0e18", t.a1, .1), .85)}!important;color:${t.text}!important}
        .radial button:hover{background:${rgba(t.a1, .3)}!important;border-color:${t.a1}!important}
        .bub.j{background:${t.panel}!important;color:${t.text}!important}
        ${t.light ? `body::after{display:none}.stage .tt,.stage .st,.say,.you{color:${t.text}!important;text-shadow:none!important}
          .rg .v,.mem b,.netrow b,.clock,.brand{color:${t.text}!important}` : ""}` : ""}`;
    if (window.ORB && window.ORB.setTheme) window.ORB.setTheme(t);
  }
  async function init(page) {
    PAGE = page || "cinema";
    try { const r = await fetch("/api/ui_theme" + (window.UTHEME_KEY ? "?k=" + window.UTHEME_KEY : "")); const j = await r.json(); apply(j.id); }
    catch (e) { apply("ironman-13"); }
  }
  function choose(id) {
    apply(id);
    fetch("/api/action", {method: "POST", headers: {"Content-Type": "application/json", "X-Jarvis": "1"}, body: JSON.stringify({action: "ui_theme", id})}).catch(() => {});
  }
  // ---------- the gallery
  function openGallery() {
    let g = document.getElementById("uthemeGal");
    if (!g) {
      g = document.createElement("div"); g.id = "uthemeGal";
      g.innerHTML = `<style>
        #uthemeGal{position:fixed;inset:0;z-index:999;background:rgba(3,4,10,.78);backdrop-filter:blur(8px);display:flex;align-items:center;justify-content:center;font-family:Inter,"Segoe UI",sans-serif}
        #uthemeGal .box{width:min(1180px,96vw);height:min(820px,92vh);background:#0b0e18;border:1px solid #273150;border-radius:24px;display:flex;flex-direction:column;overflow:hidden;color:#eef2ff;box-shadow:0 40px 120px rgba(0,0,0,.6)}
        #uthemeGal .hd{display:flex;align-items:center;gap:14px;padding:16px 20px;border-bottom:1px solid #1e2742}
        #uthemeGal .hd b{font-size:18px;letter-spacing:.5px}#uthemeGal .hd small{color:#8b95b5}
        #uthemeGal input{margin-left:auto;background:#121829;border:1px solid #273150;border-radius:12px;color:#fff;padding:9px 12px;width:240px;outline:none}
        #uthemeGal .x{border:1px solid #273150;background:#121829;color:#fff;border-radius:10px;padding:8px 12px;cursor:pointer}
        #uthemeGal .tabs{display:flex;gap:6px;padding:12px 20px;overflow-x:auto;border-bottom:1px solid #1e2742}
        #uthemeGal .tabs button{flex:0 0 auto;border:1px solid #273150;background:#121829;color:#c9d1ea;border-radius:999px;padding:7px 13px;cursor:pointer;font-size:12.5px}
        #uthemeGal .tabs button.on{background:#eef2ff;color:#0b0e18;border-color:#eef2ff}
        #uthemeGal .grid{flex:1;min-height:0;overflow-y:auto;padding:18px 20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(178px,1fr));grid-auto-rows:max-content;gap:14px;align-content:start}
        #uthemeGal .t{border-radius:16px;overflow:hidden;cursor:pointer;border:2px solid transparent;transition:transform .15s;min-height:140px;display:flex;flex-direction:column}
        #uthemeGal .tabs{flex:0 0 auto;scrollbar-width:thin}
        #uthemeGal .t:hover{transform:translateY(-3px)}#uthemeGal .t.on{border-color:#fff}
        #uthemeGal .pv{height:104px;position:relative;padding:12px}
        #uthemeGal .pv i{position:absolute;border-radius:6px}
        #uthemeGal .nm{padding:8px 10px;background:#121829;font-size:12.5px;display:flex;justify-content:space-between}
        #uthemeGal .nm small{color:#8b95b5}</style>
        <div class="box"><div class="hd"><b>🎨 Themes</b><small>${THEMES.length} looks · click to apply everywhere</small>
        <input id="uthemeQ" placeholder="Search themes…"><button class="x" id="uthemeX">✕</button></div>
        <div class="tabs" id="uthemeTabs"></div><div class="grid" id="uthemeGrid"></div></div>`;
      document.body.appendChild(g);
      g.querySelector("#uthemeX").onclick = () => g.remove();
      g.onclick = e => { if (e.target === g) g.remove(); };
      const tabs = [{id: "all", name: "All", icon: "✨"}, ...CATS];
      let cat = "all";
      const render = () => {
        const q = g.querySelector("#uthemeQ").value.toLowerCase();
        g.querySelector("#uthemeTabs").innerHTML = tabs.map(c => `<button data-c="${c.id}" class="${c.id === cat ? "on" : ""}">${c.icon || ""} ${c.name}</button>`).join("");
        g.querySelectorAll("[data-c]").forEach(b => b.onclick = () => { cat = b.dataset.c; render(); });
        const list = THEMES.filter(t => (cat === "all" || t.cat === cat) && (!q || (t.name + " " + t.catName).toLowerCase().includes(q)));
        g.querySelector("#uthemeGrid").innerHTML = list.map(t => `<div class="t ${t.id === CURRENT ? "on" : ""}" data-t="${t.id}">
          <div class="pv" style="background:${t.light ? `linear-gradient(160deg,${t.bg},${t.bg2})` : `radial-gradient(ellipse at 70% 0%,${t.bg2},${t.bg} 70%)`}">
            <i style="left:12px;top:12px;width:46px;height:80px;background:${t.panel};border:1px solid ${t.line}"></i>
            <i style="left:66px;top:12px;right:12px;height:12px;background:${t.panel};border:1px solid ${t.line}"></i>
            <i style="left:96px;top:34px;width:44px;height:44px;border-radius:50%;background:radial-gradient(circle,${t.a2},${t.a1} 55%,transparent 72%)"></i>
            <i style="left:66px;bottom:12px;width:60px;height:8px;background:linear-gradient(90deg,${t.a3},${t.a1},${t.a2})"></i>
            <i style="right:12px;bottom:12px;width:30px;height:8px;background:${t.a2}"></i></div>
          <div class="nm"><span>${t.name}</span><small>${(CATS.find(c => c.id === t.cat) || {}).icon || ""}</small></div></div>`).join("");
        g.querySelectorAll("[data-t]").forEach(el => el.onclick = () => { choose(el.dataset.t); render(); });
      };
      g.querySelector("#uthemeQ").oninput = render;
      render();
    }
  }
  window.ULTRON_THEME = {THEMES, CATS, init, apply, choose, openGallery, get current() { return CURRENT; }};
})();
