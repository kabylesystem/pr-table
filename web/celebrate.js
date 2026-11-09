const CONFETTI = ["#a3e635", "#c8ff5a", "#ff9a5a", "#ffffff", "#22d3ee"];

document.body.insertAdjacentHTML("beforeend", `
  <div class="party" id="party">
    <canvas id="confetti"></canvas>
    <div class="ring"></div><div class="ring r2"></div>
    <div class="copy"><div class="word">MERGED</div><div class="ref" id="party-ref"></div></div>
    <div class="crew" id="party-crew"></div>
  </div>`);

if (!document.querySelector('link[href*="Bungee"]')) {
  const l = document.createElement("link");
  l.rel = "stylesheet";
  l.href = "https://fonts.googleapis.com/css2?family=Bungee&display=swap";
  document.head.appendChild(l);
}

let partyTimer = null;
const partyQueue = [];

function confetti(canvas, ms) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width = innerWidth;
  const H = canvas.height = innerHeight;
  const bits = [];
  const burst = (x, y, n, power) => {
    for (let i = 0; i < n; i++) {
      const a = Math.random() * Math.PI * 2;
      const v = power * (0.4 + Math.random());
      bits.push({ x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v - power * 0.6, w: 8 + Math.random() * 10, h: 4 + Math.random() * 8,
        r: Math.random() * 6, vr: (Math.random() - 0.5) * 0.4, c: CONFETTI[Math.floor(Math.random() * CONFETTI.length)] });
    }
  };
  burst(W / 2, H * 0.42, 260, 22);
  setTimeout(() => { burst(W * 0.15, H, 120, 26); burst(W * 0.85, H, 120, 26); }, 500);
  setTimeout(() => burst(W / 2, H * 0.42, 160, 16), 1300);
  for (let t = 3000; t < ms - 2500; t += 2500) {
    setTimeout(() => {
      burst(W * (0.2 + Math.random() * 0.6), H * (0.2 + Math.random() * 0.4), 140, 18);
      burst(Math.random() < 0.5 ? W * 0.1 : W * 0.9, H, 90, 26);
    }, t);
  }
  const start = performance.now();
  const frame = now => {
    ctx.clearRect(0, 0, W, H);
    for (const b of bits) {
      b.vy += 0.45; b.vx *= 0.99; b.vy *= 0.99; b.x += b.vx; b.y += b.vy; b.r += b.vr;
      ctx.save(); ctx.translate(b.x, b.y); ctx.rotate(b.r); ctx.fillStyle = b.c;
      ctx.fillRect(-b.w / 2, -b.h / 2, b.w, b.h * Math.abs(Math.cos(b.r * 2))); ctx.restore();
    }
    if (now - start < ms) requestAnimationFrame(frame); else ctx.clearRect(0, 0, W, H);
  };
  requestAnimationFrame(frame);
}

function celebrate(ref) {
  if (partyTimer) { partyQueue.push(ref); return; }
  const party = document.getElementById("party");
  document.getElementById("party-ref").textContent = ref;
  const crew = (typeof last !== "undefined" && last && last.ui && last.ui.crew) || [];
  document.getElementById("party-crew").innerHTML = crew.map(src => `<img src="${src}" alt="">`).join("");
  party.classList.remove("on"); void party.offsetWidth; party.classList.add("on");
  document.body.classList.remove("shake"); void document.body.offsetWidth; document.body.classList.add("shake");
  confetti(document.getElementById("confetti"), 19600);
  play("merge");
  partyTimer = setTimeout(() => {
    party.classList.remove("on");
    document.body.classList.remove("shake");
    partyTimer = null;
    if (partyQueue.length) setTimeout(() => celebrate(partyQueue.shift()), 400);
  }, 20000);
}

const previewRef = new URLSearchParams(location.search).get("celebrate");
if (previewRef) setTimeout(() => celebrate(previewRef), 1500);
