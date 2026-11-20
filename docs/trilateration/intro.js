/* Intro: the dive slate floats across the page, then peels away into binary.
   Pure canvas, no dependencies. Click, Esc or the Skip button ends it. */
(function () {
  'use strict';
  var me = document.currentScript || document.querySelector('script[src$="intro.js"]');
  var HUD = me && me.dataset.mode === 'hud';
  if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var img = new Image();
  img.onload = start;
  img.src = new URL('slate.jpg', me ? me.src : location.href).href;

  function start() {
    var cv = document.createElement('canvas');
    cv.setAttribute('aria-hidden', 'true');
    cv.style.cssText = 'position:fixed;left:0;top:0;width:100%;height:100%;z-index:1000;cursor:pointer;touch-action:none';
    document.body.appendChild(cv);
    var btn = document.createElement('button');
    btn.textContent = 'Skip intro ▸';
    btn.style.cssText = 'position:fixed;right:14px;bottom:14px;z-index:1001;font:600 13px/1 ' + (HUD ? '"Courier New",monospace' : 'system-ui,sans-serif') +
      ';padding:8px 12px;cursor:pointer;border-radius:' + (HUD ? '0' : '6px') + ';border:1px solid ' + (HUD ? '#39ff14' : '#555') +
      ';background:' + (HUD ? '#021208' : '#fff') + ';color:' + (HUD ? '#39ff14' : '#222') + ';text-transform:' + (HUD ? 'uppercase' : 'none');
    document.body.appendChild(btn);
    var ctx = cv.getContext('2d');
    var W, H, dpr, sw, sh, cs, cols, rows, cells = [], done = false;

    function layout() {
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      W = innerWidth; H = innerHeight;
      cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
      var ratio = img.height / img.width;
      sh = Math.min(H * 0.7, 560); sw = sh / ratio;
      if (sw > W * 0.78) { sw = W * 0.78; sh = sw * ratio; }
      cs = Math.max(8, Math.round(sw / 30));
      cols = Math.ceil(sw / cs); rows = Math.ceil(sh / cs);
      // sample brightness per cell
      var lum = new Float32Array(cols * rows).fill(0.8);
      try {
        var oc = document.createElement('canvas'); oc.width = cols; oc.height = rows;
        var ox = oc.getContext('2d'); ox.drawImage(img, 0, 0, cols, rows);
        var d = ox.getImageData(0, 0, cols, rows).data;
        for (var i = 0; i < cols * rows; i++) lum[i] = (0.3 * d[i * 4] + 0.59 * d[i * 4 + 1] + 0.11 * d[i * 4 + 2]) / 255;
      } catch (e) { /* file:// tainted canvas: keep flat brightness */ }
      cells = [];
      var seed = 11; var rnd = function () { return (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff; };
      for (var r = 0; r < rows; r++) for (var c = 0; c < cols; c++) {
        cells.push({ c: c, r: r, u: ((c + .5) * cs / sw + (r + .5) * cs / sh) / 2, n: rnd(), bit: rnd() < .5 ? '0' : '1', vy: 0.5 + rnd(), vx: rnd() - .5, l: lum[r * cols + c], flip: rnd() });
      }
    }
    layout(); addEventListener('resize', layout);

    var T_FLY = 2.4, P0 = 3.0, SWEEP = 1.9, DUR = 1.2, T_FADE0 = 5.5, T_END = 6.9;
    var ease = function (x) { return 1 - Math.pow(1 - x, 3); };
    var t0 = performance.now();

    function end() {
      if (done) return; done = true;
      removeEventListener('resize', layout);
      cv.style.transition = 'opacity .3s'; cv.style.opacity = '0';
      setTimeout(function () { cv.remove(); btn.remove(); }, 320);
    }
    cv.addEventListener('click', end); btn.addEventListener('click', end);
    addEventListener('keydown', function (e) { if (e.key === 'Escape') end(); });

    function frame(now) {
      if (done) return;
      var t = (now - t0) / 1000;
      if (t > T_END) return end();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      var bgA = t < T_FADE0 ? 0.94 : 0.94 * Math.max(0, 1 - (t - T_FADE0) / (T_END - T_FADE0));
      ctx.fillStyle = HUD ? 'rgba(0,6,2,' + bgA + ')' : 'rgba(255,255,255,' + bgA + ')';
      ctx.fillRect(0, 0, W, H);

      // pose: fly in from the left, bob, drift up and right during the peel
      var f = ease(Math.min(t / T_FLY, 1)), after = Math.max(0, t - T_FLY);
      var cx = -sw * 0.7 + (W * 0.5 + sw * 0.7) * f + after * W * 0.035;
      var cy = H * 0.52 + Math.sin(t * 1.6) * H * 0.02 - after * H * 0.012;
      var rot = -0.34 * (1 - f) - 0.05 + Math.sin(t * 1.1) * 0.025 + after * 0.012;
      var sx = 1 - 0.14 * Math.abs(Math.sin(t * 2.4)) * (1 - f);   // faux 3D turn

      var peelFrac = Math.min(Math.max((t - P0) / (SWEEP + 0.3 + DUR), 0), 1);
      ctx.save();
      ctx.translate(cx, cy); ctx.rotate(rot); ctx.scale(sx, 1); ctx.translate(-sw / 2, -sh / 2);

      // soft shadow / glow under the slate, fading as it dissolves
      ctx.save();
      ctx.globalAlpha = 1 - peelFrac;
      ctx.shadowColor = HUD ? 'rgba(57,255,20,.85)' : 'rgba(0,0,0,.35)';
      ctx.shadowBlur = HUD ? 30 : 28; ctx.shadowOffsetY = HUD ? 0 : 14;
      ctx.fillStyle = '#dde4f0'; ctx.fillRect(0, 0, sw, sh);
      ctx.restore();

      var sxr = img.width / cols, syr = img.height / rows;
      ctx.font = '700 ' + Math.round(cs * 0.95) + 'px "Courier New",monospace';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      for (var i = 0; i < cells.length; i++) {
        var k = cells[i], x = k.c * cs, y = k.r * cs;
        var p = (t - P0 - k.u * SWEEP - k.n * 0.3) / DUR;
        if (p <= 0) {
          ctx.drawImage(img, k.c * sxr, k.r * syr, sxr, syr, x, y, cs + 0.6, cs + 0.6);
        } else if (p < 1) {
          var sliceA = p < 0.18 ? 1 - p / 0.18 : 0;
          if (sliceA > 0) { ctx.globalAlpha = sliceA; ctx.drawImage(img, k.c * sxr, k.r * syr, sxr, syr, x, y, cs + 0.6, cs + 0.6); ctx.globalAlpha = 1; }
          var a = p < 0.12 ? p / 0.12 : Math.pow(1 - (p - 0.12) / 0.88, 1.4);
          var gx = x + cs / 2 + k.vx * 60 * p, gy = y + cs / 2 - k.vy * 90 * p * p - k.vy * 14 * p;
          var bit = (k.flip + p * 3) % 1 < 0.5 ? k.bit : (k.bit === '0' ? '1' : '0');   // digits flicker
          if (HUD) ctx.fillStyle = 'rgba(57,255,20,' + (a * (0.35 + 0.65 * k.l)).toFixed(3) + ')';
          else ctx.fillStyle = 'rgba(' + (HUD ? 0 : 10) + ',' + 70 + ',' + 165 + ',' + (a * (0.35 + 0.65 * (1 - k.l * 0.6))).toFixed(3) + ')';
          ctx.fillText(bit, gx, gy);
        }
      }
      // glowing peel front
      var u = (t - P0) / SWEEP;
      if (u > 0 && u < 1.15) {
        ctx.save();
        ctx.strokeStyle = HUD ? '#9dffb4' : '#0072B2'; ctx.lineWidth = 2;
        ctx.shadowColor = HUD ? '#39ff14' : '#00a2ff'; ctx.shadowBlur = 14;
        ctx.beginPath(); ctx.moveTo(2 * u * sw, 0); ctx.lineTo(0, 2 * u * sh); ctx.stroke();
        ctx.restore();
      }
      ctx.restore();

      // caption
      var pct = Math.round(peelFrac * 100);
      ctx.save();
      ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
      ctx.globalAlpha = Math.min(1, f * 1.5) * (t > T_FADE0 ? Math.max(0, 1 - (t - T_FADE0) / 0.6) : 1);
      ctx.font = HUD ? '700 14px "Courier New",monospace' : '600 15px system-ui,sans-serif';
      ctx.fillStyle = HUD ? '#39ff14' : '#333';
      ctx.shadowColor = HUD ? '#39ff14' : 'transparent'; ctx.shadowBlur = HUD ? 8 : 0;
      var capY = Math.min(H - 20, cy + sh / 2 + 34);
      ctx.fillText(HUD ? (pct > 0 ? 'DECODING SLATE.JPG ... ' + pct + '%' : '> SLATE.JPG  LOADED') : (pct > 0 ? 'Reading the slate… ' + pct + '%' : 'The dive team’s slate'), cx, capY);
      ctx.restore();
      requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }
})();
