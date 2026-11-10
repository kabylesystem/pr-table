from playwright.sync_api import sync_playwright
CHECK = """(() => {
  const out = [];
  document.querySelectorAll('.col').forEach(c => {
    const box = c.getBoundingClientRect();
    c.querySelectorAll('.n').forEach(n => {
      const r = n.getBoundingClientRect();
      if (n.offsetParent && (r.bottom > box.bottom + 1 || r.right > box.right + 1)) out.push(n.dataset.ref + ' cut');
    });
  });
  return {n: [...document.querySelectorAll('.col')].map(c => c.style.getPropertyValue('--n')).join('/'), cut: out, visible: [...document.querySelectorAll('#review .n')].filter(n => n.offsetParent).length, more: [...document.querySelectorAll('.more')].map(m => m.textContent)};
})()"""
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:4950/?static=1")
    pg.wait_for_timeout(4000)
    print("live:", pg.evaluate(CHECK))
    for k in (7, 9, 12, 16, 22):
        pg.evaluate(f"""render({{...last, review: Array.from({{length: {k}}}, (_, i) => ({{ref: String(1900 + i), status: ['waiting','fix','decides','draft','paused'][i % 5]}})),
            ask: ['1900'], slack_msgs: {{'1900': 'x'}}, to_review: [{{ref: '1950', who: 'Toni'}}, {{ref: 'j120', who: 'Niyo'}}], next: ['870', '871', '872']}})""")
        pg.wait_for_timeout(400)
        print(k, "review rows:", pg.evaluate(CHECK))

    print("errors:", errs)
    b.close()
