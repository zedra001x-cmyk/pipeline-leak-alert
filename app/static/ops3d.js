// Live 3D operations view: a small software 3D renderer on a 2D canvas (no libraries).
// It polls GET /api/state and sends operator commands to the backend API.
(function () {
  "use strict";

  var canvas = document.getElementById("sim-canvas");
  if (!canvas) { return; }
  var ctx = canvas.getContext("2d");
  var params = new URLSearchParams(window.location.search);

  // ---------- constants ----------
  var STATUS_RGB = {
    NORMAL: [56, 176, 92], WARNING: [240, 170, 30], LEAK_SUSPECTED: [240, 100, 40],
    CRITICAL: [225, 40, 40], NO_DATA: [120, 135, 150]
  };
  var STATUS_TEXT = {
    NORMAL: "NORMAL", WARNING: "WARNING", LEAK_SUSPECTED: "LEAK SUSPECTED",
    CRITICAL: "CRITICAL", NO_DATA: "NO DATA"
  };
  var X0 = -8.3, X1 = 8.3, PIPE_Y = 1.1, PIPE_R = 0.5;
  var LEAK_POS = [0.42, 0.55, 0.38];
  var STEEL = [138, 150, 162], TANK = [92, 112, 134], SUPPORT = [70, 78, 88];
  var LIGHT = norm([-0.4, 0.9, 0.5]);
  var POLL_MS = params.get("fast") ? 400 : 1000;

  // Layers of the 3D layout. getLayers() returns them; each can be switched on or off.
  var LAYERS = [
    { id: "ground", label: "Ground", on: true }, { id: "structures", label: "Tanks and supports", on: true },
    { id: "pipeline", label: "Pipeline", on: true }, { id: "flow", label: "Oil flow", on: true },
    { id: "assets", label: "Valves and pump", on: true }, { id: "sensors", label: "Sensors", on: true },
    { id: "alerts", label: "Leaks and alerts", on: true }, { id: "labels", label: "Labels", on: true }
  ];
  function getLayers() { return LAYERS; }
  function layerOn(id) { return LAYERS.some(function (l) { return l.id === id && l.on; }); }
  var state = null;

  // ---------- small vector helpers ----------
  function sub(a, b) { return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]; }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function cross(a, b) {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  }
  function norm(a) {
    var l = Math.sqrt(dot(a, a)) || 1;
    return [a[0] / l, a[1] / l, a[2] / l];
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function rand(lo, hi) { return lo + Math.random() * (hi - lo); }
  function mix(a, b, t) {
    return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  }
  function rgba(c, a) {
    return "rgba(" + (c[0] | 0) + "," + (c[1] | 0) + "," + (c[2] | 0) + "," + a + ")";
  }

  // ---------- scene state ----------
  var sections = [];
  [0, 1, 2].forEach(function (i) {
    var len = (X1 - X0) / 3;
    sections.push({
      id: null, name: "Section " + "ABC".charAt(i), status: "NO_DATA",
      pressure: null, flowIn: null, flowOut: null,
      x0: X0 + i * len, x1: X0 + (i + 1) * len, leakPos: LEAK_POS[i],
      seeds: Array.apply(null, Array(16)).map(function () { return Math.random(); }),
      spray: [], spill: 0
    });
  });

  var cam = { yaw: -0.55, pitch: 0.38, dist: 30, target: [0, 1.4, 0] };
  var userTouched = false;
  var W = 0, H = 0, dpr = 1, focal = 600, narrow = false;
  var STATUS_SHORT = { NORMAL: "NORMAL", WARNING: "WARNING", LEAK_SUSPECTED: "LEAK?", CRITICAL: "CRITICAL", NO_DATA: "NO DATA" };
  var camPos = [0, 0, 0], camF = [0, 0, 1], camR = [1, 0, 0], camU = [0, 1, 0];

  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    var rect = canvas.getBoundingClientRect();
    W = Math.max(200, Math.round(rect.width * dpr));
    H = Math.max(200, Math.round(rect.height * dpr));
    canvas.width = W;
    canvas.height = H;
    var wasNarrow = narrow;
    narrow = rect.width < 520;
    if (narrow !== wasNarrow && !userTouched) {
      cam.pitch = narrow ? 0.5 : 0.38;
      cam.dist = narrow ? 29 : 30;
      cam.target[1] = narrow ? 3.0 : 1.4;
    }
  }

  function setupCamera() {
    var cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    camPos = [
      cam.target[0] + cam.dist * cp * Math.sin(cam.yaw),
      cam.target[1] + cam.dist * sp,
      cam.target[2] + cam.dist * cp * Math.cos(cam.yaw)
    ];
    camF = norm(sub(cam.target, camPos));
    camR = norm(cross(camF, [0, 1, 0]));
    camU = cross(camR, camF);
    focal = Math.min(W * 1.05, H * 2.2);
  }

  // returns [screenX, screenY, depth] or null if behind camera
  function project(p) {
    var rel = sub(p, camPos);
    var z = dot(rel, camF);
    if (z < 0.2) { return null; }
    return [W / 2 + focal * dot(rel, camR) / z, H / 2 - focal * dot(rel, camU) / z, z];
  }

  // ---------- per-frame draw list ----------
  var items = [];

  function shade(base, n, center) {
    var v = norm(sub(camPos, center));
    var diff = Math.max(0, dot(n, LIGHT));
    var h = norm([LIGHT[0] + v[0], LIGHT[1] + v[1], LIGHT[2] + v[2]]);
    var spec = Math.pow(Math.max(0, dot(n, h)), 28) * 0.45;
    var k = 0.28 + 0.85 * diff;
    return [
      clamp(base[0] * k + 255 * spec, 0, 255),
      clamp(base[1] * k + 255 * spec, 0, 255),
      clamp(base[2] * k + 255 * spec, 0, 255)
    ];
  }

  function pushQuad(verts, n, base, emissive) {
    var cx = 0, cy = 0, cz = 0, i;
    for (i = 0; i < verts.length; i++) { cx += verts[i][0]; cy += verts[i][1]; cz += verts[i][2]; }
    var c = [cx / verts.length, cy / verts.length, cz / verts.length];
    if (dot(n, sub(camPos, c)) <= 0) { return; } // back-face culling
    var pts = [];
    for (i = 0; i < verts.length; i++) {
      var s = project(verts[i]);
      if (!s) { return; }
      pts.push(s);
    }
    var col = shade(base, n, c);
    if (emissive) { col = mix(col, emissive, 0.55); }
    items.push({ t: "poly", d: dot(sub(c, camPos), camF), pts: pts, fill: rgba(col, 1) });
  }

  // cylinder lying along the X axis (pipes, flanges, bands)
  function addCylinderX(xa, xb, cy, cz, rad, sides, segs, base, emissive) {
    for (var s = 0; s < sides; s++) {
      var a0 = 2 * Math.PI * s / sides, a1 = 2 * Math.PI * (s + 1) / sides, am = (a0 + a1) / 2;
      var n = [0, Math.sin(am), Math.cos(am)];
      for (var g = 0; g < segs; g++) {
        var xs = xa + (xb - xa) * g / segs, xe = xa + (xb - xa) * (g + 1) / segs;
        pushQuad([
          [xs, cy + rad * Math.sin(a0), cz + rad * Math.cos(a0)],
          [xe, cy + rad * Math.sin(a0), cz + rad * Math.cos(a0)],
          [xe, cy + rad * Math.sin(a1), cz + rad * Math.cos(a1)],
          [xs, cy + rad * Math.sin(a1), cz + rad * Math.cos(a1)]
        ], n, base, emissive);
      }
    }
  }

  // upright cylinder (storage tanks) with a top cap
  function addTank(cx, cz, y0, y1, rad, sides) {
    var s, a0, a1, am, n;
    for (s = 0; s < sides; s++) {
      a0 = 2 * Math.PI * s / sides; a1 = 2 * Math.PI * (s + 1) / sides; am = (a0 + a1) / 2;
      n = [Math.cos(am), 0, Math.sin(am)];
      var bands = 3;
      for (var b = 0; b < bands; b++) {
        var ya = y0 + (y1 - y0) * b / bands, yb = y0 + (y1 - y0) * (b + 1) / bands;
        pushQuad([
          [cx + rad * Math.cos(a0), ya, cz + rad * Math.sin(a0)],
          [cx + rad * Math.cos(a1), ya, cz + rad * Math.sin(a1)],
          [cx + rad * Math.cos(a1), yb, cz + rad * Math.sin(a1)],
          [cx + rad * Math.cos(a0), yb, cz + rad * Math.sin(a0)]
        ], n, b === 1 ? mix(TANK, [30, 40, 52], 0.35) : TANK, null);
      }
    }
    var cap = [];
    for (s = 0; s < sides; s++) {
      a0 = 2 * Math.PI * s / sides;
      cap.push([cx + rad * Math.cos(a0), y1, cz + rad * Math.sin(a0)]);
    }
    var pts = [], ok = true;
    cap.forEach(function (p) { var q = project(p); if (q) { pts.push(q); } else { ok = false; } });
    if (ok) {
      var col = shade(mix(TANK, [200, 210, 220], 0.25), [0, 1, 0], [cx, y1, cz]);
      items.push({ t: "poly", d: dot(sub([cx, y1, cz], camPos), camF) - 0.4, pts: pts, fill: rgba(col, 1) });
    }
  }

  function addBox(cx, y0, cz, w, h, d, base, emissive) {
    var hx = w / 2, hz = d / 2, y1 = y0 + h;
    var v = [
      [cx - hx, y0, cz - hz], [cx + hx, y0, cz - hz], [cx + hx, y0, cz + hz], [cx - hx, y0, cz + hz],
      [cx - hx, y1, cz - hz], [cx + hx, y1, cz - hz], [cx + hx, y1, cz + hz], [cx - hx, y1, cz + hz]
    ];
    pushQuad([v[4], v[5], v[6], v[7]], [0, 1, 0], base, emissive);
    pushQuad([v[0], v[1], v[5], v[4]], [0, 0, -1], base, emissive);
    pushQuad([v[2], v[3], v[7], v[6]], [0, 0, 1], base, emissive);
    pushQuad([v[1], v[2], v[6], v[5]], [1, 0, 0], base, emissive);
    pushQuad([v[3], v[0], v[4], v[7]], [-1, 0, 0], base, emissive);
  }

  function addDot(p, worldR, fill, depthBias) {
    var s = project(p);
    if (!s) { return; }
    items.push({ t: "dot", d: s[2] - (depthBias || 0), x: s[0], y: s[1],
                 r: Math.max(1, worldR * focal / s[2]), fill: fill });
  }

  // ---------- scene building ----------
  function buildScene(time) {
    items = [];
    var pipeY = PIPE_Y;
    if (layerOn("structures")) {
      addTank(-10.5, 0, 0, 3.4, 2.2, 28);
      addTank(10.5, 0, 0, 3.4, 2.2, 28);
    }
    sections.forEach(function (sec) {
      var rgb = STATUS_RGB[sec.status] || STATUS_RGB.NO_DATA;
      var mid = (sec.x0 + sec.x1) / 2;
      var pulse = sec.status === "NORMAL" || sec.status === "NO_DATA" ? 0.5 : 0.55 + 0.45 * Math.sin(time * 6);
      if (layerOn("pipeline")) {
        addCylinderX(sec.x0, sec.x1, pipeY, 0, PIPE_R, 20, 4,
          mix(STEEL, rgb, sec.status === "NORMAL" ? 0.12 : 0.3), null);
      }
      if (layerOn("sensors")) {
        addCylinderX(mid - 0.2, mid + 0.2, pipeY, 0, PIPE_R + 0.05, 20, 1,
          mix(rgb, [255, 255, 255], 0.1), mix(rgb, [255, 255, 255], 0.2 * pulse));
        addBox(mid, pipeY + PIPE_R + 0.02, 0, 0.5, 0.35, 0.5, [52, 60, 70], null);
        addDot([mid, pipeY + PIPE_R + 0.5, 0], 0.09, rgba(mix(rgb, [255, 255, 255], 0.4 * pulse), 1), 0);
      }
      if (layerOn("structures")) {
        [0.2, 0.8].forEach(function (f) {
          var sx = sec.x0 + (sec.x1 - sec.x0) * f;
          addBox(sx, 0, 0, 0.45, pipeY - PIPE_R, 0.7, SUPPORT, null);
          addBox(sx, 0, 0, 1.0, 0.1, 1.5, [58, 64, 72], null);
        });
      }
      if (layerOn("flow")) {
        var len = sec.x1 - sec.x0, fin = sec.flowIn == null ? 0 : sec.flowIn;
        var ratio = fin > 0 ? clamp((sec.flowOut == null ? fin : sec.flowOut) / fin, 0, 1) : 0;
        var speed = fin > 0 ? 2.4 * (fin / 1000) / len : 0;
        var flowCol = mix(rgb, [255, 255, 255], 0.5);
        if (fin > 5) {
          sec.seeds.forEach(function (seed, k) {
            var t = (seed + time * speed) % 1;
            if (t > sec.leakPos && seed > ratio) { return; }
            addDot([sec.x0 + t * len, pipeY + PIPE_R + 0.03, (k % 3 - 1) * 0.12], 0.1, rgba(flowCol, 0.95), 0.6);
          });
        }
      }
    });
    if (layerOn("pipeline")) {
      [X0, sections[0].x1, sections[1].x1, X1].forEach(function (x) {
        addCylinderX(x - 0.1, x + 0.1, pipeY, 0, PIPE_R + 0.14, 20, 1, [95, 104, 116], null);
      });
    }
    if (layerOn("assets")) { addAssets(time); }
  }

  var VALVE_X = [X0, 0, 0, X1];
  function assetColor(a) {
    if (a.pending) { return [240, 170, 30]; }
    return (a.state === "OPEN" || a.state === "RUNNING") ? [56, 176, 92] : [225, 40, 40];
  }
  function addAssets(time) {
    if (!state) { return; }
    VALVE_X[1] = sections[0].x1; VALVE_X[2] = sections[1].x1;
    state.assets.forEach(function (a) {
      var col = assetColor(a);
      if (a.kind === "VALVE") {
        var x = VALVE_X[a.position], top = PIPE_Y + PIPE_R;
        addBox(x, top, 0, 0.45, 0.45, 0.45, [58, 66, 78], null);
        addBox(x, top + 0.45, 0, 0.12, 0.35, 0.12, [90, 98, 110], null);
        addBox(x, top + 0.8, 0, 1.0, 0.1, 1.0, col, col);
      } else {
        addBox(-7.2, 0, 1.7, 1.5, 0.9, 1.0, [58, 66, 78], null);
        addCylinderX(-8.2, -6.2, 0.45, 1.7, 0.42, 16, 2, [75, 85, 98], col);
        if (a.state === "RUNNING") {
          addDot([-7.2 + Math.cos(time * 8) * 0.3, 1.0, 1.7 + Math.sin(time * 8) * 0.3], 0.08, "rgba(255,255,255,0.9)", 0.5);
        }
      }
    });
  }

  // ---------- ground layer ----------
  function polyPath(points) {
    ctx.beginPath();
    for (var i = 0; i < points.length; i++) {
      if (i === 0) { ctx.moveTo(points[i][0], points[i][1]); } else { ctx.lineTo(points[i][0], points[i][1]); }
    }
    ctx.closePath();
  }

  function groundPoly(world, y) {
    var out = [];
    for (var i = 0; i < world.length; i++) {
      var p = project([world[i][0], y, world[i][1]]);
      if (!p) { return null; }
      out.push(p);
    }
    return out;
  }

  function drawGround(time) {
    var sky = ctx.createLinearGradient(0, 0, 0, H);
    sky.addColorStop(0, "#0b1a2b");
    sky.addColorStop(0.55, "#2a4258");
    sky.addColorStop(1, "#6b7f8c");
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, W, H);
    if (!layerOn("ground")) { return; }

    var g = groundPoly([[-90, -90], [90, -90], [90, 90], [-90, 90]], 0);
    if (g) {
      polyPath(g);
      ctx.fillStyle = "#1c242b";
      ctx.fill();
    }
    // grid
    ctx.lineWidth = Math.max(1, dpr * 0.6);
    ctx.strokeStyle = "rgba(160,190,210,0.12)";
    var i, a, b;
    for (i = -20; i <= 20; i += 2) {
      a = project([i, 0, -12]); b = project([i, 0, 12]);
      if (a && b) { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
    }
    for (i = -12; i <= 12; i += 2) {
      a = project([-20, 0, i]); b = project([20, 0, i]);
      if (a && b) { ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); }
    }
    // soft shadows
    var sh = groundPoly([[X0, -0.9], [X1, -0.9], [X1, 0.9], [X0, 0.9]], 0.01);
    if (sh) { polyPath(sh); ctx.fillStyle = "rgba(0,0,0,0.35)"; ctx.fill(); }
    [-10.5, 10.5].forEach(function (cx) {
      var pts = [];
      for (var k = 0; k < 28; k++) {
        var an = 2 * Math.PI * k / 28;
        pts.push([cx + 2.6 * Math.cos(an) + 0.5, 2.6 * Math.sin(an) + 0.3]);
      }
      var gp = groundPoly(pts, 0.01);
      if (gp) { polyPath(gp); ctx.fillStyle = "rgba(0,0,0,0.3)"; ctx.fill(); }
    });
    // spills
    sections.forEach(function (sec) {
      if (sec.spill < 0.05 || !layerOn("alerts")) { return; }
      var cx = sec.x0 + (sec.x1 - sec.x0) * sec.leakPos, pts = [];
      for (var k = 0; k < 28; k++) {
        var an = 2 * Math.PI * k / 28;
        var r = sec.spill * (1 + 0.18 * Math.sin(3 * an + sec.leakPos * 9) + 0.1 * Math.sin(5 * an));
        pts.push([cx + r * 1.25 * Math.cos(an), 0.4 + r * Math.sin(an)]);
      }
      var gp = groundPoly(pts, 0.02);
      if (gp) {
        polyPath(gp);
        ctx.fillStyle = "rgba(14,9,5,0.88)";
        ctx.fill();
        ctx.strokeStyle = "rgba(255,150,50,0.35)";
        ctx.lineWidth = dpr;
        ctx.stroke();
      }
    });
  }

  // ---------- leak spray physics ----------
  function leakIntensity(sec) {
    if (sec.status !== "LEAK_SUSPECTED" && sec.status !== "CRITICAL") { return 0; }
    var diff = (sec.flowIn || 0) - (sec.flowOut || 0);
    return clamp(diff / 320, 0.25, 1);
  }

  function stepSpray(dt) {
    sections.forEach(function (sec) {
      var inten = leakIntensity(sec);
      var lx = sec.x0 + (sec.x1 - sec.x0) * sec.leakPos;
      if (inten > 0) {
        var n = inten * 70 * dt + Math.random();
        for (var k = 0; k < n && sec.spray.length < 160; k++) {
          sec.spray.push({
            p: [lx, PIPE_Y + PIPE_R, 0],
            v: [rand(-0.9, 0.9), rand(2.2, 3.4) + inten * 3.2, rand(-1.0, 1.0)]
          });
        }
        sec.spill = Math.min(3.0, sec.spill + inten * 0.22 * dt);
      } else {
        sec.spill = Math.max(0, sec.spill - 0.12 * dt);
      }
      for (var i = sec.spray.length - 1; i >= 0; i--) {
        var q = sec.spray[i];
        q.v[1] -= 9 * dt;
        q.p[0] += q.v[0] * dt; q.p[1] += q.v[1] * dt; q.p[2] += q.v[2] * dt;
        if (q.p[1] <= 0.02) { sec.spray.splice(i, 1); }
      }
    });
  }

  function addSprayItems() {
    if (!layerOn("alerts")) { return; }
    sections.forEach(function (sec) {
      sec.spray.forEach(function (q) {
        addDot(q.p, 0.07, "rgba(58,34,14,0.92)", 0.3);
      });
    });
  }

  // ---------- overlay (rings, labels) ----------
  function drawRing(center, radius, color, alpha) {
    var pts = [], i, p;
    for (i = 0; i <= 28; i++) {
      var an = 2 * Math.PI * i / 28;
      p = project([center[0], center[1] + radius * Math.sin(an), center[2] + radius * Math.cos(an)]);
      if (!p) { return; }
      pts.push(p);
    }
    ctx.beginPath();
    pts.forEach(function (q, k) { if (k === 0) { ctx.moveTo(q[0], q[1]); } else { ctx.lineTo(q[0], q[1]); } });
    ctx.strokeStyle = rgba(color, alpha);
    ctx.lineWidth = 3 * dpr;
    ctx.stroke();
  }

  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawLabel(worldPos, lines, accent, dim) {
    var s = project(worldPos);
    if (!s) { return; }
    var fs = Math.round(clamp(12 * dpr * (30 / s[2]) * 0.9, 10 * dpr, 16 * dpr));
    ctx.font = "600 " + fs + "px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
    var wMax = 0;
    lines.forEach(function (l) { wMax = Math.max(wMax, ctx.measureText(l).width); });
    var pad = fs * 0.5, lh = fs * 1.25, bw = wMax + pad * 2, bh = lines.length * lh + pad;
    var x = s[0] - bw / 2, y = s[1] - bh;
    ctx.fillStyle = dim ? "rgba(15,24,34,0.6)" : "rgba(15,24,34,0.82)";
    roundRect(x, y, bw, bh, fs * 0.4);
    ctx.fill();
    ctx.fillStyle = rgba(accent, 1);
    roundRect(x, y, fs * 0.25, bh, fs * 0.12);
    ctx.fill();
    ctx.textBaseline = "middle";
    ctx.textAlign = "left";
    lines.forEach(function (l, i) {
      ctx.fillStyle = i === 0 ? "#ffffff" : (i === 1 ? rgba(mix(accent, [255, 255, 255], 0.35), 1) : "#c3cfda");
      ctx.fillText(l, x + pad + fs * 0.2, y + pad / 2 + lh * (i + 0.5));
    });
    ctx.strokeStyle = "rgba(255,255,255,0.35)";
    ctx.lineWidth = dpr;
    ctx.beginPath(); ctx.moveTo(s[0], s[1]); ctx.lineTo(s[0], s[1] + fs * 0.9); ctx.stroke();
  }

  function drawOverlay(time) {
    sections.forEach(function (sec, idx) {
      var rgb = STATUS_RGB[sec.status] || STATUS_RGB.NO_DATA;
      var mid = (sec.x0 + sec.x1) / 2, inten = leakIntensity(sec);
      if (layerOn("alerts")) {
        if (inten > 0) {
          var lx = sec.x0 + (sec.x1 - sec.x0) * sec.leakPos, pr = 0.5 + 0.5 * Math.sin(time * 8);
          drawRing([lx, PIPE_Y, 0], PIPE_R + 0.2 + 0.25 * pr, rgb, 0.9 - 0.5 * pr);
          if (!narrow) { drawLabel([lx, PIPE_Y + 3.6, 0], ["LEAK"], rgb, false); }
        } else if (sec.status === "WARNING") {
          var pw = 0.5 + 0.5 * Math.sin(time * 5);
          drawRing([mid, PIPE_Y, 0], PIPE_R + 0.15 + 0.15 * pw, rgb, 0.8 - 0.4 * pw);
        }
      }
      if (layerOn("labels")) {
        var lines;
        if (narrow) {
          lines = [sec.name.toUpperCase().replace("SECTION ", "SEC "), STATUS_SHORT[sec.status] || sec.status];
          if (sec.pressure != null) { lines.push(sec.pressure.toFixed(0) + " bar"); }
        } else {
          lines = [sec.name.toUpperCase(), STATUS_TEXT[sec.status] || sec.status];
          if (sec.pressure != null) {
            lines.push(sec.pressure.toFixed(1) + " bar  |  \u0394 " + (sec.flowIn - sec.flowOut).toFixed(0) + " L/min");
          }
        }
        drawLabel([mid, PIPE_Y + (narrow ? (idx === 1 ? 3.9 : 2.2) : 2.1), 0], lines, rgb, false);
      }
    });
    if (layerOn("labels")) {
      drawLabel([-10.5, narrow ? 3.5 : 3.9, 0], ["DEPOT A"], [120, 150, 180], true);
      drawLabel([10.5, narrow ? 3.5 : 3.9, 0], ["DEPOT B"], [120, 150, 180], true);
      if (state && !narrow && layerOn("assets")) {
        state.assets.forEach(function (a) {
          var pos = a.kind === "PUMP" ? [-7.2, 2.0, 1.7] : [VALVE_X[a.position], PIPE_Y + 2.4, 0];
          drawLabel(pos, [a.name, a.pending ? "PENDING" : a.state], assetColor(a), true);
        });
      }
    }
  }

  // ---------- main render loop ----------
  var lastT = 0;
  function frame(ts) {
    var time = ts / 1000, dt = Math.min(0.05, lastT ? time - lastT : 0.016);
    lastT = time;
    if (!userTouched && autoRotate.checked) { cam.yaw += dt * 0.07; }
    setupCamera();
    stepSpray(dt);
    drawGround(time);
    buildScene(time);
    addSprayItems();
    items.sort(function (a, b) { return b.d - a.d; });
    items.forEach(function (it) {
      if (it.t === "poly") {
        polyPath(it.pts);
        ctx.fillStyle = it.fill;
        ctx.fill();
        ctx.strokeStyle = it.fill;
        ctx.lineWidth = 0.6;
        ctx.stroke();
      } else {
        ctx.fillStyle = it.fill;
        ctx.beginPath();
        ctx.arc(it.x, it.y, it.r, 0, 6.2832);
        ctx.fill();
      }
    });
    drawOverlay(time);
    requestAnimationFrame(frame);
  }

  // ---------- camera controls (drag = orbit, wheel/pinch = zoom) ----------
  var pointers = {}, lastPinch = 0;
  canvas.addEventListener("pointerdown", function (e) {
    canvas.setPointerCapture(e.pointerId);
    pointers[e.pointerId] = [e.clientX, e.clientY];
    userTouched = true;
  });
  canvas.addEventListener("pointermove", function (e) {
    if (!pointers[e.pointerId]) { return; }
    var ids = Object.keys(pointers);
    var prev = pointers[e.pointerId];
    if (ids.length === 1) {
      cam.yaw -= (e.clientX - prev[0]) * 0.008;
      cam.pitch = clamp(cam.pitch + (e.clientY - prev[1]) * 0.006, 0.1, 1.35);
    }
    pointers[e.pointerId] = [e.clientX, e.clientY];
    if (ids.length === 2) {
      var a = pointers[ids[0]], b = pointers[ids[1]];
      var d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (lastPinch) { cam.dist = clamp(cam.dist * (lastPinch / d), 12, 55); }
      lastPinch = d;
    }
  });
  function endPointer(e) { delete pointers[e.pointerId]; lastPinch = 0; }
  canvas.addEventListener("pointerup", endPointer);
  canvas.addEventListener("pointercancel", endPointer);
  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    userTouched = true;
    cam.dist = clamp(cam.dist * (1 + Math.sign(e.deltaY) * 0.08), 12, 55);
  }, { passive: false });

  // ---------- live data + controls (everything goes through the backend API) ----------
  var statusEl = document.getElementById("sim-status");
  var toggleBtn = document.getElementById("sim-toggle");
  var autoRotate = document.getElementById("sim-rotate");
  var alertsBody = document.getElementById("sim-alerts");
  var assetBox = document.getElementById("asset-controls");
  var autoIso = document.getElementById("auto-isolate");
  var meta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = meta ? meta.content : "";
  var role = document.body.dataset.role || "viewer";
  var canOperate = role === "operator" || role === "admin";
  var assetBtns = {};

  function setStatus(msg) { if (statusEl) { statusEl.textContent = msg; } }

  function api(method, url, body) {
    return fetch(url, {
      method: method, credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF },
      body: body === undefined ? undefined : JSON.stringify(body)
    }).then(function (r) {
      if (r.status === 401) { window.location.href = "/login"; }
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) { throw new Error(typeof d.detail === "string" ? d.detail : "Request failed (" + r.status + ")"); }
        return d;
      });
    });
  }

  function act(method, url, body) {
    return api(method, url, body).then(fetchState).catch(function (e) { setStatus("Action failed: " + e.message); });
  }

  function renderAlerts(list) {
    if (!alertsBody) { return; }
    while (alertsBody.firstChild) { alertsBody.removeChild(alertsBody.firstChild); }
    if (!list.length) {
      var li0 = document.createElement("li"); li0.textContent = "No alerts yet."; alertsBody.appendChild(li0); return;
    }
    list.forEach(function (a) {
      var li = document.createElement("li");
      var badge = document.createElement("span");
      badge.className = "badge status-" + a.severity.toLowerCase().replace(/_/g, "-");
      badge.textContent = STATUS_TEXT[a.severity] || a.severity;
      li.appendChild(badge);
      li.appendChild(document.createTextNode(" " + a.message + " [" + a.status.toLowerCase() + "]"));
      if (a.status === "ACTIVE" && canOperate) {
        var b = document.createElement("button");
        b.type = "button"; b.className = "btn secondary"; b.textContent = "Acknowledge";
        b.addEventListener("click", function () { act("POST", "/api/alerts/" + a.id + "/acknowledge"); });
        li.appendChild(b);
      }
      alertsBody.appendChild(li);
    });
  }

  function nextAction(a) {
    if (a.kind === "VALVE") { return a.state === "OPEN" ? "CLOSE" : "OPEN"; }
    return a.state === "RUNNING" ? "STOP" : "START";
  }

  function renderAssets(list) {
    if (!assetBox) { return; }
    list.forEach(function (a) {
      var b = assetBtns[a.id];
      if (!b) {
        b = document.createElement("button");
        b.type = "button"; b.className = "btn secondary";
        b.addEventListener("click", function () {
          var cur = assetBtns[a.id].asset;
          act("POST", "/api/assets/" + cur.id + "/command", { action: nextAction(cur) });
        });
        assetBox.appendChild(b); assetBtns[a.id] = b;
      }
      b.asset = a;
      b.disabled = !canOperate || a.pending;
      b.textContent = a.name + ": " + (a.pending ? "pending..." : a.state + " (tap to " + nextAction(a).toLowerCase() + ")");
    });
  }

  var tankRows = [];
  function renderTanks(t) {
    var list = document.getElementById("tank-list"), avg = document.getElementById("tank-avg");
    if (!list) { return; }
    if (avg) { avg.textContent = t.average == null ? "n/a" : t.average.toFixed(1) + "%"; }
    t.levels.forEach(function (v, i) {
      var row = tankRows[i];
      if (!row) {
        var li = document.createElement("li"), label = document.createElement("span"), m = document.createElement("meter");
        m.min = 0; m.max = 100;
        li.appendChild(label); li.appendChild(m); list.appendChild(li);
        row = tankRows[i] = { label: label, meter: m };
      }
      row.meter.value = v;
      row.label.textContent = "Tank " + (i + 1) + ": " + v.toFixed(1) + "%";
    });
  }

  function applyState(st) {
    state = st;
    st.sections.forEach(function (s, i) {
      var v = sections[i];
      if (!v) { return; }
      v.id = s.id; v.name = s.name; v.status = s.status;
      v.pressure = s.pressure; v.flowIn = s.flow_in; v.flowOut = s.flow_out;
    });
    renderAssets(st.assets);
    renderAlerts(st.alerts);
    if (st.tanks) { renderTanks(st.tanks); }
    document.querySelectorAll(".sim-only").forEach(function (el) { el.hidden = st.mode !== "SIMULATION"; });
    if (toggleBtn) { toggleBtn.textContent = st.sim.running ? "Pause" : "Resume"; }
    document.querySelectorAll(".needs-operator").forEach(function (el) { el.disabled = !canOperate; });
    if (autoIso) { autoIso.checked = st.auto_isolation; autoIso.disabled = role !== "admin"; }
    setStatus((st.mode === "SIMULATION" ? "Simulation mode" : "Live devices") + " | active alerts: " +
      st.summary.active_alerts + " | devices online: " + st.devices.online + "/" + st.devices.total);
  }

  function fetchState() { return api("GET", "/api/state").then(applyState); }

  function loop() {
    fetchState().catch(function (e) { setStatus("Connection problem: " + e.message); })
      .then(function () { setTimeout(loop, POLL_MS); });
  }

  function init() {
    var layerBox = document.getElementById("layer-list");
    if (layerBox) {
      getLayers().forEach(function (layer) {
        var label = document.createElement("label"); label.className = "check";
        var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = layer.on;
        cb.addEventListener("change", function () { layer.on = cb.checked; });
        label.appendChild(cb); label.appendChild(document.createTextNode(" " + layer.label));
        layerBox.appendChild(label);
      });
    }
    loop();
  }

  if (toggleBtn) {
    toggleBtn.addEventListener("click", function () {
      act("POST", "/api/sim/" + (state && state.sim.running ? "pause" : "resume"));
    });
  }
  document.querySelectorAll("[data-leak]").forEach(function (btn) {
    btn.addEventListener("click", function () { act("POST", "/api/sim/leak", { section: parseInt(btn.dataset.leak, 10) }); });
  });
  document.querySelectorAll("[data-heat]").forEach(function (btn) {
    btn.addEventListener("click", function () { act("POST", "/api/sim/overheat", { section: parseInt(btn.dataset.heat, 10) }); });
  });
  var repair = document.getElementById("sim-repair");
  if (repair) { repair.addEventListener("click", function () { act("POST", "/api/sim/repair"); }); }
  if (autoIso) {
    autoIso.addEventListener("change", function () { act("POST", "/api/settings/auto-isolation", { enabled: autoIso.checked }); });
  }
  var camReset = document.getElementById("cam-reset");
  if (camReset) {
    camReset.addEventListener("click", function () {
      cam.yaw = -0.55; cam.pitch = narrow ? 0.5 : 0.38; cam.dist = narrow ? 29 : 30; userTouched = false;
    });
  }

  if (window.ResizeObserver) { new ResizeObserver(resize).observe(canvas); }
  window.addEventListener("resize", resize);
  resize();
  init();
  requestAnimationFrame(frame);
})();
