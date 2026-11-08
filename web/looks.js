const LOOK_FONTS = {
  1: "Monoton",
  2: "Unbounded:wght@900",
  3: "Major+Mono+Display",
  4: "Geist+Mono:wght@400",
  5: "Bungee",
};

function applyLook(look) {
  if (!LOOK_FONTS[look]) return;
  document.documentElement.dataset.look = look;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `https://fonts.googleapis.com/css2?family=${LOOK_FONTS[look]}&display=swap`;
  document.head.appendChild(link);
  const brand = document.getElementById("brand");
  if (look === "2") brand.textContent = "PR TABLE";
  if (look === "3") brand.innerHTML = `<span data-text="pr table">pr table</span>`;
  if (look === "4") {
    brand.innerHTML = [..."PR TABLE"].map(c => `<div class="tile ${c === " " ? "gap" : ""}"></div>`).join("");
    const tiles = [...brand.querySelectorAll(".tile")];
    const word = [..."PR TABLE"];
    const glyphs = "ABCDEFGHIJKLMNOPQRSTUVWXYZ#0123456789";
    const roll = () => tiles.forEach((t, i) => {
      if (word[i] === " ") return;
      let n = 4 + i * 2;
      const step = () => {
        t.classList.remove("flip"); void t.offsetWidth; t.classList.add("flip");
        setTimeout(() => { t.textContent = n > 0 ? glyphs[Math.floor(Math.random() * glyphs.length)] : word[i]; if (n-- > 0) setTimeout(step, 90); }, 250);
      };
      step();
    });
    roll();
    setInterval(roll, 45000);
  }
  if (look === "5") brand.textContent = "PR TABLE";
}

applyLook(new URLSearchParams(location.search).get("look") || document.documentElement.dataset.defaultLook);

function fitFigs() {
  const figs = document.querySelector(".col.next .figs");
  const list = document.getElementById("next");
  if (!figs || getComputedStyle(figs).display === "none") return;
  const col = figs.parentElement.getBoundingClientRect();
  const room = col.bottom - list.getBoundingClientRect().bottom - 28;
  figs.style.height = `${Math.max(140, Math.min(560, room))}px`;
}

setInterval(fitFigs, 500);
addEventListener("resize", fitFigs);
