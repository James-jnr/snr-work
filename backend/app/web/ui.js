"use strict";
/* Shared helpers for every page: API calls, key handling, header with the live heart,
   Sync now, Phone link, and the image popup (lightbox). */
const SNR = (() => {
  const $ = (s, r = document) => r.querySelector(s);
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (k === "class") e.className = v;
      else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
      else if (v !== null && v !== undefined && v !== false) e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c.nodeType ? c : document.createTextNode(c));
    return e;
  }

  let apiKey = "";
  try { apiKey = localStorage.getItem("snr_key") || ""; } catch (e) {}
  const u = new URL(location.href);                       // phone QR link carries ?key=...
  if (u.searchParams.get("key")) {
    apiKey = u.searchParams.get("key");
    try { localStorage.setItem("snr_key", apiKey); } catch (e) {}
    u.searchParams.delete("key");
    history.replaceState(null, "", u.pathname + (u.search || "") + u.hash);
  }
  const withKey = (path) => apiKey ? path + (path.includes("?") ? "&" : "?") + "key=" + encodeURIComponent(apiKey) : path;

  function keyBox(msg) {
    const b = $("#keybox"); if (!b) return;
    b.style.display = "flex"; $("#keyerr").textContent = msg || "";
  }
  async function api(path, opts = {}) {
    const r = await fetch(path, {
      method: opts.method || "GET",
      headers: Object.assign({"Content-Type": "application/json"}, apiKey ? {"X-API-Key": apiKey} : {}),
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (r.status === 401 || r.status === 503) { keyBox(apiKey ? "Wrong key" : "This server needs your API key"); throw new Error("auth"); }
    if (!r.ok) { let t = r.status; try { t = (await r.json()).detail; } catch (e) {} const err = new Error(t); err.status = r.status; throw err; }
    const bx = $("#keybox"); if (bx) bx.style.display = "none";
    return r.json();
  }

  const fmtTime = (epoch) => epoch ? new Date(epoch * 1000).toLocaleString([], {weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : "";
  const fmtClock = (epoch) => new Date(epoch * 1000).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  const dur = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60); return h ? `${h}h ${m}m` : `${m}m ${s % 60}s`; };

  /* ---------- popup image viewer ---------- */
  function lightbox(items, start = 0) {
    let i = start;
    const img = el("img"), cap = el("div", {class: "cap"});
    const close = () => { document.removeEventListener("keydown", onKey); root.remove(); };
    const show = (n) => { i = (n + items.length) % items.length; img.src = items[i].src; cap.textContent = items[i].caption + (items.length > 1 ? `   (${i + 1}/${items.length})` : ""); };
    const onKey = (e) => { if (e.key === "Escape") close(); else if (e.key === "ArrowRight") show(i + 1); else if (e.key === "ArrowLeft") show(i - 1); };
    const root = el("div", {class: "lb", onclick: (e) => { if (e.target === root) close(); }},
      img, cap, el("button", {class: "x", title: "Close (Esc)", onclick: close}, "✕"),
      items.length > 1 ? el("button", {class: "prev", title: "Previous", onclick: () => show(i - 1)}, "‹") : null,
      items.length > 1 ? el("button", {class: "next", title: "Next", onclick: () => show(i + 1)}, "›") : null);
    document.body.append(root); document.addEventListener("keydown", onKey); show(i);
  }

  /* ---------- phone link ---------- */
  async function phone() {
    const box = el("div", {class: "box"}, el("b", {}, "Open on your phone"), el("div", {class: "small"}, "loading…"));
    const root = el("div", {class: "modal", onclick: (e) => { if (e.target === root) root.remove(); }}, box);
    document.body.append(root);
    try {
      const d = await api("/api/phone");
      const inp = el("input", {class: "url", readonly: true, value: d.url});
      const qr = el("div", {class: "qr"}); qr.innerHTML = d.qr_svg || "";
      box.replaceChildren(el("b", {}, "Open on your phone"),
        el("div", {class: "small"}, "Scan the code, or type the link into your phone's browser. Your phone must be on the same Wi-Fi as this PC."),
        d.qr_svg ? qr : el("div", {class: "small"}, "(QR code needs the 'segno' package: pip install segno)"), inp,
        el("div", {class: "row", style: "margin-top:10px"},
          el("button", {class: "btn ghost", onclick: () => { inp.select(); navigator.clipboard && navigator.clipboard.writeText(d.url); }}, "Copy link"),
          el("span", {class: "spacer"}), el("button", {class: "btn", onclick: () => root.remove()}, "Close")),
        el("div", {class: "small", style: "margin-top:8px"}, d.note));
    } catch (e) { box.replaceChildren(el("b", {}, "Phone link"), el("div", {class: "err"}, "Could not get the link: " + e.message), el("button", {class: "btn", onclick: () => root.remove()}, "Close")); }
  }

  /* ---------- header: nav, live heart, next 4-hour close, Sync now ---------- */
  let nextClose = null;
  function header(active) {
    const h = $("#hdr");
    const heart = el("span", {class: "heart", id: "heart"}, "♥");
    const label = el("span", {id: "hlabel"}, "checking MT5…"), sub = el("span", {class: "sub", id: "hsub"});
    const sync = el("button", {class: "btn", id: "syncbtn", title: "Ask MT5 to send its latest data now", onclick: doSync}, "Sync now");
    h.className = "hdr";
    h.replaceChildren(
      el("span", {class: "brand"}, "SNR Alerts"),
      el("nav", {class: "nav"},
        el("a", {href: "/ui", class: active === "alerts" ? "on" : ""}, "Alerts"),
        el("a", {href: "/charts", class: active === "charts" ? "on" : ""}, "Charts"),
        el("button", {onclick: phone}, "Phone link")),
      el("span", {class: "spacer"}),
      el("span", {class: "pulse", title: "Glows red while MT5 is sending data"}, heart, label, sub),
      sync);
    const kb = el("div", {class: "keybox", id: "keybox"}, el("input", {id: "keyin", type: "password", placeholder: "API key (API_KEY from your .env)"}),
      el("button", {class: "btn", onclick: () => { apiKey = $("#keyin").value.trim(); try { localStorage.setItem("snr_key", apiKey); } catch (e) {} location.reload(); }}, "Use key"),
      el("span", {class: "err", id: "keyerr"}));
    h.after(kb);
    poll(); setInterval(poll, 5000); setInterval(tick, 1000);
  }
  async function poll() {
    try {
      const s = await api("/api/status");
      nextClose = s.next_h4_close_utc;
      $("#heart").classList.toggle("live", s.live);
      const ages = s.symbols.map(x => x.age).filter(x => x !== null);
      $("#hlabel").textContent = s.live ? "MT5 live" : (ages.length ? "MT5 offline" : "waiting for MT5");
      $("#hlabel").style.color = s.live ? "" : "var(--muted)";
      if (!$("#limitbar")) { const lb = el("div", {class: "banner", id: "limitbar", style: "display:none"}); $("#hdr").after(lb); }
      const lim = s.limits || {}; const lbx = $("#limitbar");
      lbx.style.display = lim.blocked ? "block" : "none"; lbx.textContent = lim.blocked ? "⛔ " + lim.blocked : "";
      const sb = $("#syncbtn"); if (sb && !sb.dataset.busy) sb.disabled = false;
      tick();
    } catch (e) { if (e.message !== "auth") { $("#heart").classList.remove("live"); $("#hlabel").textContent = "backend not reachable"; } }
  }
  function tick() {
    const sub = $("#hsub"); if (!sub) return;
    if (!nextClose) { sub.textContent = ""; return; }
    const left = nextClose - Date.now() / 1000;
    sub.textContent = left > 0 ? `· check again at ${fmtClock(nextClose)} (next 4-hour close, in ${dur(left)})` : "· 4-hour candle just closed: check now";
  }
  async function doSync() {
    const b = $("#syncbtn"); b.disabled = true; b.dataset.busy = "1"; b.textContent = "Syncing…";
    const done = (txt) => { b.textContent = txt; setTimeout(() => { b.textContent = "Sync now"; b.disabled = false; delete b.dataset.busy; }, 2500); };
    try {
      const r = await api("/api/sync", {method: "POST"});
      const t0 = Date.now();
      while (Date.now() - t0 < (r.expect_within_sec + 20) * 1000) {
        await new Promise(r => setTimeout(r, 2000));
        const s = await api("/api/status");
        if (!s.sync_pending) { window.dispatchEvent(new Event("snr-synced")); return done("Synced ✓"); }
      }
      done("No reply from MT5");
    } catch (e) { done(e.message === "auth" ? "Key needed" : (e.message || "Sync failed")); }
  }

  return {$, el, api, withKey, fmtTime, fmtClock, dur, lightbox, phone, header, get key() { return apiKey; }};
})();
