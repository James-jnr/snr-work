// Drives every page in a simulated browser. Start the server on port 8765 with simulated data first:
//   API_KEY=test UI_PUBLIC=true uvicorn app.main:app --port 8765   then   python scripts/simulate.py http://127.0.0.1:8765 test
// Then:  npm install jsdom   and   node scripts/ui_smoke.js
const {JSDOM, VirtualConsole} = require("jsdom");
const BASE = "http://127.0.0.1:8765";
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
let fails = 0;
const check = (n, c, i="") => { if (!c) fails++; console.log((c ? "PASS " : "FAIL ") + n + (i !== "" ? "  " + i : "")); };

async function page(path, wait = 1500) {
  const errors = [];
  const vc = new VirtualConsole(); vc.on("jsdomError", e => errors.push(String(e.message || e).slice(0, 200))); vc.on("error", e => errors.push(String(e).slice(0, 200)));
  const dom = await JSDOM.fromURL(BASE + path, {runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      w.fetch = (u, o) => fetch(new URL(u, BASE + "/"), o);
      w.matchMedia = () => ({matches: false, addListener() {}, addEventListener() {}});
      w.URL.createObjectURL = () => "blob:x"; w.URL.revokeObjectURL = () => {};
      w.HTMLElement.prototype.scrollIntoView = () => {};
    }});
  await sleep(wait);
  return {dom, w: dom.window, d: dom.window.document, errors};
}

(async () => {
  // ---- list page
  let p = await page("/ui");
  check("list: no script errors", p.errors.length === 0, p.errors.join(" | "));
  const rows = p.d.querySelectorAll("table.list tr.row");
  check("list: rows rendered", rows.length >= 1, rows.length);
  check("list: heart is live", p.d.querySelector("#heart").classList.contains("live"));
  check("list: next 4-hour close shown", /check again at/.test(p.d.querySelector("#hsub").textContent), p.d.querySelector("#hsub").textContent);
  check("list: nav has Alerts, Charts, Phone link", ["Alerts", "Charts", "Phone link"].every(t => p.d.querySelector("#hdr").textContent.includes(t)));
  check("list: story-line chips use full words", /Monthly (bullish|bearish)|Weekly (bullish|bearish)/.test(p.d.querySelector("table.list").textContent));
  // phone modal
  [...p.d.querySelectorAll(".nav button")].find(b => b.textContent === "Phone link").click(); await sleep(800);
  check("phone: modal with link and QR", !!p.d.querySelector(".modal input.url") && !!p.d.querySelector(".modal .qr svg"), (p.d.querySelector(".modal input.url") || {}).value);
  p.d.querySelector(".modal .btn:last-of-type").click();
  check("phone: popup closes", !p.d.querySelector(".modal"));
  const id = rows[0].querySelector("td:nth-child(5)") && Number((await (await fetch(BASE + "/api/alerts?limit=1")).json()).alerts[0].id);
  // sync button
  p.d.querySelector("#syncbtn").click(); await sleep(600);
  check("sync: button shows progress", /Syncing/.test(p.d.querySelector("#syncbtn").textContent), p.d.querySelector("#syncbtn").textContent);
  p.w.close();

  // ---- single alert page
  p = await page("/alert/" + id, 2500);
  check("alert: no script errors", p.errors.length === 0, p.errors.join(" | "));
  const secs = [...p.d.querySelectorAll("[data-sec]")].map(e => e.dataset.sec);
  check("alert: report split into sections", secs.some(s => s.startsWith("STORY")) && secs.some(s => s.startsWith("KEY LEVELS")), secs.join(", "));
  check("alert: setups shown as separate cards", p.d.querySelectorAll(".setup:not(.scn)").length === 3, p.d.querySelectorAll(".setup:not(.scn)").length);
  check("alert: two scenario cards (one buy, one sell)", p.d.querySelectorAll(".setup.scn").length === 2 && p.d.querySelectorAll(".setup.scn.buy").length === 1 && p.d.querySelectorAll(".setup.scn.sell").length === 1, p.d.querySelectorAll(".setup.scn").length);
  check("alert: thesis card present", [...p.d.querySelectorAll("[data-sec]")].some(e => e.dataset.sec.startsWith("THESIS")));
  check("alert: kill-zone note kept out of the cards", /London and New York kill zones/.test(p.d.body.textContent));
  check("alert: verify checkbox per story line", p.d.querySelectorAll(".verify input[type=checkbox]").length >= 1, p.d.querySelectorAll(".verify input[type=checkbox]").length);
  check("alert: no glossary text", !/Glossary/.test(p.d.body.textContent));
  const imgs = p.d.querySelectorAll(".grid img");
  check("alert: charts grid incl. zone map", imgs.length >= 4 && /zone map/i.test(p.d.querySelector(".grid figcaption").textContent), imgs.length);
  imgs[1].click(); await sleep(200);
  check("popup: lightbox opens on click", !!p.d.querySelector(".lb img"));
  const cap1 = p.d.querySelector(".lb .cap").textContent;
  p.d.dispatchEvent(new p.w.KeyboardEvent("keydown", {key: "ArrowRight"})); await sleep(100);
  check("popup: arrow key moves to next chart", p.d.querySelector(".lb .cap").textContent !== cap1);
  p.d.dispatchEvent(new p.w.KeyboardEvent("keydown", {key: "Escape"})); await sleep(100);
  check("popup: Esc closes", !p.d.querySelector(".lb"));
  // verify a story line through the UI
  const cb = p.d.querySelector(".verify input[type=checkbox]"); cb.checked = true; cb.dispatchEvent(new p.w.Event("change")); await sleep(900);
  check("verify: ticking shows 'Verified by you'", /Verified by you/.test(p.d.querySelector(".verify").textContent));
  const rejBtn = [...p.d.querySelectorAll(".verify button")].find(b => /Reject/.test(b.textContent)); rejBtn.click(); await sleep(900);
  check("verify: reject shows 'treated as ended'", /treated as ended/.test(p.d.querySelector(".verify").textContent));
  [...p.d.querySelectorAll(".verify button")].find(b => /Undo/.test(b.textContent)).click(); await sleep(700);
  check("verify: undo restores", /Unverified/.test(p.d.querySelector(".verify").textContent));
  p.w.close();

  // ---- thesis page
  p = await page("/thesis", 1800);
  check("thesis: no script errors", p.errors.length === 0, p.errors.join(" | "));
  check("thesis: card with scenarios and history", p.d.querySelectorAll(".card .setup").length === 2 && /How the plan changed/.test(p.d.body.textContent), p.d.querySelectorAll(".card .setup").length);
  check("thesis: nav link present", [...p.d.querySelectorAll(".nav a")].some(a => a.textContent === "Thesis"));
  p.w.close();

  // ---- charts page
  p = await page("/charts", 2500);
  check("charts: no script errors", p.errors.length === 0, p.errors.join(" | "));
  const tabs = [...p.d.querySelectorAll("#tabs button")].map(b => b.textContent);
  check("charts: tabs Zone map, Monthly, Weekly, Daily, 4-hour, 1-hour", tabs.join(",") === "Zone map,Monthly,Weekly,Daily,4-hour,1-hour", tabs.join(","));
  [...p.d.querySelectorAll("#tabs button")].find(b => b.textContent === "Weekly").click(); await sleep(1800);
  check("charts: levels table with names + strength", p.d.querySelectorAll("#rows tr").length >= 1 && /\/100/.test(p.d.querySelector("#rows").textContent), p.d.querySelectorAll("#rows tr").length);
  p.w.close();
  console.log(fails ? "\nSOME UI CHECKS FAILED" : "\nALL UI CHECKS PASSED");
  process.exit(fails ? 1 : 0);
})().catch(e => { console.error("HARNESS ERROR", e); process.exit(2); });
