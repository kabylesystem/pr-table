from playwright.sync_api import sync_playwright
CHECK = """(() => {
  const out = [];
  document.querySelectorAll('.col').forEach(c => {
    const box = c.getBoundingClientRect();
    c.querySelectorAll('.n').forEach(n => {
      const r = n.getBoundingClientRect();
      const right = Math.max(r.right, ...[...n.querySelectorAll('*')].filter(e => e.offsetParent).map(e => e.getBoundingClientRect().right));
      if (n.offsetParent && (r.bottom > box.bottom + 1 || right > box.right + 1)) out.push(n.dataset.ref + ' cut');
    });
  });
  return {n: [...document.querySelectorAll('.col')].map(c => c.style.getPropertyValue('--n')).join('/'), cut: out, visible: [...document.querySelectorAll('#review .n')].filter(n => n.offsetParent).length, more: [...document.querySelectorAll('.more')].map(m => m.textContent)};
})()"""
MIXES = {
    "quiet": "['waiting','fix','decides','prep'][i % 4]",
    "loud": "['waiting','fix','decides','draft','paused'][i % 5]",
    "long": "['frozen','conflict','ci','waiting'][i % 4]",
}
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:4950/?static=1")
    pg.wait_for_timeout(4000)
    print("live:", pg.evaluate(CHECK))
    sizes = {}
    for k in (4, 7, 9, 12, 16, 22):
        for name, mix in MIXES.items():
            asks = "ask: ['1900', '1901', '1902', '1903'], ask_repost: ['1901', '1902', '1903'], slack_msgs: {'1900': 'x', '1901': 'x', '1902': 'x', '1903': 'x'}" if name == "loud" else "ask: [], ask_repost: []"
            to_review = "[{ref: '1950', who: 'Toni'}, {ref: 'j120', who: 'Niyo'}]" if name != "quiet" else "[]"
            pg.evaluate(f"""render({{...last, review: Array.from({{length: {k}}}, (_, i) => ({{ref: String(1900 + i), status: {mix}}})), {asks}, next: ['870', '871', '872']}})""")
            pg.wait_for_timeout(300)
            r = pg.evaluate(CHECK)
            sizes.setdefault(k, set()).add(r["n"])
            if r["cut"] or r["more"]:
                print("CUT", k, name, r)
    for k, v in sizes.items():
        print(k, "rows ->", " | ".join(sorted(v)), "" if len(v) == 1 else "  UNSTABLE")
    print("errors:", errs)
    b.close()
