/**
 * Pure Canvas2D particle engine — Selora AI signature gold sparkles.
 * Zero dependencies, ~3KB. Ports the visual from selorahomes.com/sparkles.js
 * (tsparticles) into a lightweight custom element for the HA panel.
 */

const TAU = Math.PI * 2;
const FRAME_INTERVAL = 1000 / 60; // 60 fps cap

function rand(min, max) {
  return Math.random() * (max - min) + min;
}

function parseHexColor(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

class SparkleEngine {
  constructor(canvas, opts) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.dpr = window.devicePixelRatio || 1;
    this.particles = [];
    this.rafId = 0;
    this.lastFrame = 0;
    this.w = 0;
    this.h = 0;
    this.color = opts.color || "#C7AE6A";
    this.count = opts.count || 400;
    this.maxOpacity = opts.maxOpacity ?? 1.0;
    this._rgb = parseHexColor(this.color);
  }

  resize(width, height) {
    this.w = width;
    this.h = height;
    this.canvas.width = width * this.dpr;
    this.canvas.height = height * this.dpr;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  init() {
    this.particles = [];
    for (let i = 0; i < this.count; i++) {
      // Bias Y toward top: square the random value so more particles
      // spawn near y=0 (the glow line), fewer near the bottom.
      const yBias = Math.random();
      this.particles.push({
        x: rand(0, this.w),
        y: yBias * yBias * this.h,
        vx: rand(-1, 1),
        vy: rand(-1, 1),
        size: rand(0.4, 1.4),
        opacity: rand(0.1, this.maxOpacity),
        opacitySpeed: rand(0.008, 0.03),
        opacityDir: Math.random() > 0.5 ? 1 : -1,
      });
    }
  }

  renderStatic() {
    this.init();
    this._draw();
  }

  start() {
    this.init();
    this.lastFrame = 0;
    this._loop(0);
  }

  _loop = (ts) => {
    this.rafId = requestAnimationFrame(this._loop);
    if (ts - this.lastFrame < FRAME_INTERVAL) return;
    this.lastFrame = ts;
    this._update();
    this._draw();
  };

  _update() {
    const { w, h, maxOpacity, particles } = this;
    for (let i = 0, len = particles.length; i < len; i++) {
      const p = particles[i];
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0) p.x = w;
      else if (p.x > w) p.x = 0;
      if (p.y < 0) {
        // Respawn at bottom, will drift back up
        p.y = h;
      } else if (p.y > h) {
        // Respawn near top with bias (attracted to glow)
        const r = Math.random();
        p.y = r * r * h * 0.5;
      }
      p.opacity += p.opacitySpeed * p.opacityDir;
      if (p.opacity >= maxOpacity) {
        p.opacity = maxOpacity;
        p.opacityDir = -1;
      } else if (p.opacity <= 0.1) {
        p.opacity = 0.1;
        p.opacityDir = 1;
      }
    }
  }

  _draw() {
    const { ctx, w, h, particles, _rgb } = this;
    const [r, g, b] = _rgb;
    ctx.clearRect(0, 0, w, h);
    for (let i = 0, len = particles.length; i < len; i++) {
      const p = particles[i];
      ctx.globalAlpha = p.opacity;
      ctx.fillStyle = `rgb(${r},${g},${b})`;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size, 0, TAU);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  destroy() {
    cancelAnimationFrame(this.rafId);
    this.rafId = 0;
    this.particles = [];
    this.ctx = null;
    this.canvas = null;
  }
}

class SeloraParticles extends HTMLElement {
  connectedCallback() {
    // Build DOM
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-hidden", "true");
    canvas.setAttribute("role", "presentation");
    canvas.style.cssText =
      "position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none;touch-action:none;";
    this.appendChild(canvas);
    this._canvas = canvas;

    // Read properties set by Lit's .prop=${} bindings (already on the element)
    const count = this.count || 400;
    const color = this.color || "#C7AE6A";
    const maxOpacity = this.maxOpacity ?? 1.0;

    this._engine = new SparkleEngine(canvas, { color, count, maxOpacity });

    // Wait for layout to settle, then start
    this._ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        if (width > 0 && height > 0) {
          this._engine?.resize(width, height);
          if (!this._started) {
            this._start();
          }
        }
      }
    });
    this._ro.observe(this);
  }

  _start() {
    if (!this._engine) return;
    this._started = true;

    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    if (reducedMotion) {
      this._engine.renderStatic();
    } else {
      this._engine.start();
    }

    // Trigger CSS fade-in
    this.classList.add("visible");
  }

  disconnectedCallback() {
    this._ro?.disconnect();
    this._ro = null;
    this._engine?.destroy();
    this._engine = null;
    this._started = false;
    if (this._canvas) {
      this._canvas.remove();
      this._canvas = null;
    }
  }
}

customElements.define("selora-particles", SeloraParticles);
