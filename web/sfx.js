const SFX = {
  merge: { src: "sfx/merge-crowd.mp3", volume: 1 },
  reviewer: { src: "sfx/reviewer-ding.mp3", volume: 0.9 },
  ci: { src: "sfx/ci-groan.mp3", volume: 0.6 },
};

let muted = new URLSearchParams(location.search).get("mute") === "1";
try { muted = muted || localStorage.getItem("pr-table-mute") === "1"; } catch (e) {}

const sfxCache = {};
for (const [name, s] of Object.entries(SFX)) {
  const a = new Audio(s.src);
  a.preload = "auto";
  a.volume = s.volume;
  sfxCache[name] = a;
}

function play(name) {
  if (muted || !sfxCache[name]) return;
  const a = sfxCache[name];
  a.currentTime = 0;
  a.play().catch(() => {});
}

function stopSound(name) {
  const a = sfxCache[name];
  if (a) a.pause();
}

addEventListener("keydown", e => {
  if (e.key !== "m") return;
  muted = !muted;
  try { localStorage.setItem("pr-table-mute", muted ? "1" : "0"); } catch (err) {}
  if (muted) Object.keys(sfxCache).forEach(stopSound);
});
