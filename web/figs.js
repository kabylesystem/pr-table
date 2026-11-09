let CREW = [];

function setCrew(list) {
  if (JSON.stringify(list) === JSON.stringify(CREW)) return;
  CREW = list;
  document.querySelectorAll(".stage, .brand .inline").forEach(el => el.remove());
  if (document.documentElement.dataset.fig) document.body.insertAdjacentHTML("beforeend", `<div class="stage"></div>`);
}

window.setCrew = setCrew;

function box(sel) {
  return document.querySelector(sel).getBoundingClientRect();
}

function imgs(list, cls, style = () => "") {
  return list.map((src, i) => `<img class="${cls}" src="${src}" style="${style(i)}">`).join("");
}

function placeFigs(layout) {
  const stage = document.querySelector(".stage");
  if (!CREW.length || !stage) return;
  const brand = document.getElementById("brand");
  const agents = document.getElementById("agents");
  const H = innerHeight;
  const W = innerWidth;
  const bar = box("#bar");
  const onBar = H - bar.top - 2;
  if (layout === "1") {
    if (!brand.querySelector(".inline")) brand.insertAdjacentHTML("beforeend", imgs(CREW, "inline"));
    return;
  }
  if (layout === "5") {
    if (!brand.querySelector(".inline")) {
      brand.insertAdjacentHTML("afterbegin", imgs(CREW.slice(0, 2), "inline"));
      brand.insertAdjacentHTML("beforeend", imgs(CREW.slice(2), "inline mirror"));
    }
    return;
  }
  if (layout === "2") {
    const next = box("#next");
    const nextCol = box(".col.next");
    const room = Math.max(160, Math.min(440, nextCol.bottom - next.bottom - 24));
    const h = 230;
    stage.innerHTML = `
      <div style="position:absolute; left:${nextCol.left}px; bottom:${H - nextCol.bottom}px; height:${room}px; display:flex; align-items:flex-end; gap:8px">${imgs(CREW.slice(0, 2), "crew")}</div>
      <div style="position:absolute; right:${W - bar.right + 88}px; bottom:${onBar}px; height:${h}px; display:flex; align-items:flex-end; gap:8px">${imgs(CREW.slice(2), "crew mirror")}</div>`;
    agents.style.paddingRight = `${88 + h * 1.15}px`;
    return;
  }
  if (layout === "3") {
    const h = 230;
    stage.innerHTML = `<div style="position:absolute; right:${W - bar.right + 88}px; bottom:${onBar}px; height:${h}px; display:flex; align-items:flex-end">${imgs(CREW, "crew", i => i ? "margin-left:-22px" : "")}</div>`;
    agents.style.paddingRight = `${88 + h * 2.1}px`;
    return;
  }
  if (layout === "4") {
    agents.style.paddingRight = `${W * 0.52}px`;
    const size = `${W}x${H}`;
    let walker = stage.querySelector(".walker");
    if (!walker || walker.dataset.size !== size) {
      stage.innerHTML = `<div class="walker" data-size="${size}" style="position:absolute; height:150px; display:flex; gap:10px; visibility:hidden">${imgs(CREW, "", i => `animation-delay:-${i * 0.15}s`)}</div>`;
      walker = stage.querySelector(".walker");
      const lane = () => {
        const from = W * 0.56;
        const to = Math.max(from, W - 88 - walker.offsetWidth);
        walker.style.setProperty("--from", `${from}px`);
        walker.style.setProperty("--to", `${to}px`);
        walker.style.visibility = "visible";
      };
      Promise.all([...walker.querySelectorAll("img")].map(img => img.decode().catch(() => {}))).then(lane);
    }
    walker.style.bottom = `${onBar}px`;
  }
}

function applyFigs(layout) {
  if (!["1", "2", "3", "4", "5"].includes(layout)) return;
  document.documentElement.dataset.fig = layout;
  const brand = document.getElementById("brand");
  brand.innerHTML = `<span class="word">${brand.textContent}</span>`;
  document.body.insertAdjacentHTML("beforeend", `<div class="stage"></div>`);
  const run = () => placeFigs(layout);
  run();
  setInterval(run, 1000);
  addEventListener("resize", run);
}

applyFigs(new URLSearchParams(location.search).get("fig") || document.documentElement.dataset.defaultFig);
