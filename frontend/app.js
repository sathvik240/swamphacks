/* PlanIT Control supervisor dashboard (no build step). */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COLORS = { low: "#2e9d5b", moderate: "#9bbf2f", elevated: "#e0a91b", high: "#e46a1f", extreme: "#c9252c" };
  const REPORT_ICONS = { wildlife: "🐊", flooding: "🌊", soft_ground: "🟫", downed_line: "⚡", heat_illness: "🥵", fire: "🔥", lightning: "🌩️", terrain: "⛰️", other: "⚠️" };

  const state = { sites: [], selected: null, detail: null, markers: {}, reportMarkers: {}, pin: null };

  // ---------- map ----------
  const map = L.map("map", { zoomControl: true }).setView([33, -90], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 18, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  const strikeLayer = L.layerGroup().addTo(map);
  const reportLayer = L.layerGroup().addTo(map);

  function siteIcon(s) {
    const lvl = s.level || "low";
    const size = 22 + Math.round((s.index || 0) * 1.6);
    const stop = s.lightning_all_clear_at ? " stopwork" : "";
    return L.divIcon({
      className: "",
      html: `<div class="site-marker${stop}" style="width:${size}px;height:${size}px;background:${COLORS[lvl]}">${s.index != null ? Math.round(s.index) : "…"}</div>`,
      iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    });
  }

  function drawMarkers() {
    for (const s of state.sites) {
      let m = state.markers[s.id];
      if (!m) {
        m = L.marker([s.lat, s.lon]).addTo(map).on("click", () => select(s.id));
        state.markers[s.id] = m;
      }
      m.setIcon(siteIcon(s));
      m.setZIndexOffset(Math.round((s.index || 0) * 100));
      m.bindTooltip(`${esc(s.name)} (${esc(s.state)}) · ${s.index ?? "–"}/10`);
    }
  }

  function addReportMarker(r) {
    if (state.reportMarkers[r.id]) return;
    const icon = L.divIcon({ className: "report-marker", html: REPORT_ICONS[r.hazard_type] || "⚠️", iconSize: [24, 24], iconAnchor: [12, 12] });
    const img = r.photo_url ? `<br><img src="${esc(r.photo_url)}" style="width:160px;border-radius:6px;margin-top:4px">` : "";
    state.reportMarkers[r.id] = L.marker([r.lat, r.lon], { icon }).addTo(reportLayer)
      .bindPopup(`<b>${esc(r.hazard_label)}</b> (sev ${r.severity})<br>${esc(r.description)}<br><small>${esc(r.reporter || "crew")} · ${new Date(r.created_at).toLocaleString()}</small>${img}`);
  }

  // ---------- ranking list ----------
  let lastListHtml = "";
  function renderList() {
    const q = $("#search").value.toLowerCase();
    const list = $("#site-list");
    const html = state.sites
      .filter((s) => !q || `${s.name} ${s.state} ${s.utility} ${s.river || ""}`.toLowerCase().includes(q))
      .map((s, i) => {
        const sub = s.lightning_all_clear_at
          ? `<span class="stopw">⚡ STOP WORK</span>`
          : s.top_alert ? `<span class="warn">${esc(s.top_alert)}</span>` : esc(s.top_factor ? `Driver: ${s.top_factor}` : s.utility);
        return `<li data-id="${s.id}" class="${s.id === state.selected ? "sel" : ""}">
          <span class="rank">${i + 1}</span>
          <span class="chip ${s.level || "none"}">${s.index ?? "–"}</span>
          <span><div class="site-name">${esc(s.name)}, ${esc(s.state)}</div><div class="site-sub">${sub}</div></span>
        </li>`;
      }).join("");
    // Only touch the DOM when something changed, so clicks aren't lost mid-refresh.
    if (html !== lastListHtml) list.innerHTML = lastListHtml = html;
  }
  $("#site-list").addEventListener("click", (e) => {
    const li = e.target.closest("li");
    if (li) select(li.dataset.id, true);
  });
  $("#search").addEventListener("input", renderList);

  function renderStats() {
    const counts = { extreme: 0, high: 0 };
    let stop = 0, alerts = 0;
    for (const s of state.sites) {
      if (s.level in counts) counts[s.level]++;
      if (s.lightning_all_clear_at) stop++;
      alerts += s.alerts;
    }
    const src = state.sites.find((s) => s.source)?.source;
    $("#stats").innerHTML = `
      <span class="stat"><b>${state.sites.length}</b> sites</span>
      ${stop ? `<span class="stat stop">⚡ <b>${stop}</b> stop-work</span>` : ""}
      <span class="stat">Extreme <b>${counts.extreme}</b> · High <b>${counts.high}</b></span>
      <span class="stat"><b>${alerts}</b> active alerts</span>
      ${src ? `<span class="stat">Data: ${esc(src)}</span>` : ""}`;
  }

  async function loadSites() {
    state.sites = await (await fetch("/api/sites")).json();
    drawMarkers(); renderList(); renderStats();
  }

  function upsertSite(s) {
    const i = state.sites.findIndex((x) => x.id === s.id);
    if (i >= 0) state.sites[i] = s; else state.sites.push(s);
    state.sites.sort((a, b) => (b.index ?? -1) - (a.index ?? -1) || b.alerts - a.alerts || a.name.localeCompare(b.name));
    // A refresh cycle sends one update per site; redraw once per burst.
    clearTimeout(renderTimer);
    renderTimer = setTimeout(() => { drawMarkers(); renderList(); renderStats(); }, 250);
  }
  let renderTimer;

  // ---------- detail panel ----------
  async function select(id, fly) {
    state.selected = id;
    renderList();
    const s = state.sites.find((x) => x.id === id);
    if (fly && s) map.flyTo([s.lat, s.lon], 10, { duration: 0.8 });
    await loadDetail();
  }

  async function loadDetail() {
    if (!state.selected) return;
    const r = await fetch(`/api/sites/${state.selected}`);
    state.detail = await r.json();
    renderDetail();
    for (const rep of state.detail.reports) addReportMarker(rep);
  }

  function fmtCountdown(iso) {
    const ms = new Date(iso) - Date.now();
    if (ms <= 0) return "00:00";
    const m = Math.floor(ms / 60000), sec = Math.floor((ms % 60000) / 1000);
    return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  }

  function renderDetail() {
    const el = $("#detail");
    const d = state.detail;
    if (!d) { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    const s = d.site, c = d.conditions, h = d.hazard;
    const cur = c?.current;
    const alerts = d.alerts.sort((a, b) => (a.kind === "lightning" ? -1 : b.kind === "lightning" ? 1 : 0));
    el.innerHTML = `
      <div class="detail-head">
        <div><h3>${esc(s.name)}, ${esc(s.state)}</h3>
          <div class="muted small">${esc(s.plant_type)} · ${esc(s.utility)} · ${esc(s.county)} Co.${s.river ? ` · ${esc(s.river)}` : ""}${s.coastal ? " · coastal" : ""}</div></div>
        <button class="close" id="close-detail" aria-label="Close">✕</button>
      </div>
      ${h ? `<div class="gauge"><span class="big chip ${h.level}">${h.index}</span>
        <div><b>${h.level.toUpperCase()}</b> hazard index<div class="muted small">ML model ${h.ml_score}/10${h.overlays.length ? " + live signals" : ""} · ${esc(c.source)} data · ${esc(c.local_time.slice(11, 16))} local</div></div></div>` : `<p class="muted">Waiting for first conditions refresh…</p>`}
      ${alerts.length ? `<div class="section"><h4>Active alerts</h4>${alerts.map(alertHtml).join("")}</div>` : ""}
      ${cur ? `<div class="section"><h4>Conditions now</h4><div class="grid">
        ${metric("Heat index", `${cur.heat_index_f}°F`)}${metric("Temp / RH", `${cur.temp_f}°F / ${cur.humidity}%`)}
        ${metric("Wind gusts", `${cur.wind_gust_mph} mph`)}${metric("Thunder", cur.thunder ? "YES" : "no")}
        ${metric("Rain 24h / 72h", `${c.precip_24h_in} / ${c.precip_72h_in} in`)}${metric("Overnight rain", `${c.overnight_precip_in} in`)}
        ${metric("Soil moisture", `${cur.soil_moisture} m³/m³`)}${metric("CAPE", `${cur.cape} J/kg`)}
      </div>${c.nws_alerts.length ? `<div class="small" style="margin-top:6px">NWS: ${c.nws_alerts.map((a) => esc(a.event)).join(", ")}</div>` : ""}</div>` : ""}
      ${h ? `<div class="section"><h4>What's driving the score</h4>
        ${h.factors.filter((f) => f.contribution > 0.05).slice(0, 5).map((f) => `<div class="bar"><span>${esc(f.label)}</span><span class="track"><span class="fill" style="width:${Math.min(100, f.contribution * 10)}%;display:block"></span></span><span class="num">+${f.contribution}</span></div>`).join("") || `<div class="muted small">No significant weather drivers.</div>`}
        ${h.overlays.map((o) => `<div class="overlay">● ${esc(o.label)} <span class="muted">(${esc(o.effect)})</span></div>`).join("")}
      </div>
      <div class="section"><h4>Forecast hazard</h4><div class="forecast">${h.forecast.map((f, i) => `<div><div class="muted">${i === 0 ? "Today" : i === 1 ? "Tomorrow" : new Date(f.date + "T12:00").toLocaleDateString(undefined, { weekday: "short" })}</div><span class="chip ${f.level}">${f.index}</span></div>`).join("")}</div></div>` : ""}
      <div class="section"><h4>Next-day briefing</h4>
        ${d.briefing ? `<div class="card"><b>${esc(d.briefing.headline)}</b><pre>${esc(d.briefing.text.split("\n").slice(2).join("\n"))}</pre></div>` : `<div class="muted small">Sends at 6:00 PM local.</div>`}
        <button class="ghost" id="brief-now" style="margin-top:6px">Generate briefing now</button>
      </div>
      <div class="section"><h4>Crew reports (5 mi, 30 days)</h4>
        ${d.reports.length ? d.reports.map(reportHtml).join("") : `<div class="muted small">No reports yet.</div>`}
        <button id="report-here" style="margin-top:6px">＋ Report hazard here</button>
      </div>
      <div class="section"><h4>Demo scenarios</h4><div class="demo">
        <button class="ghost" data-sc="storm">⚡ Lightning storm</button><button class="ghost" data-sc="heavy_rain">🌧 Night of heavy rain</button>
        <button class="ghost" data-sc="heat">🌡 Heat wave</button><button class="ghost" data-sc="wildfire">🔥 Red flag</button>
        <button class="ghost" data-sc="clear">Clear</button></div></div>`;
    $("#close-detail").onclick = () => { state.selected = null; state.detail = null; renderDetail(); renderList(); };
    $("#report-here").onclick = () => openReport(s.lat + 0.004, s.lon + 0.004, s.id);
    $("#brief-now").onclick = async () => { await fetch(`/api/sites/${s.id}/briefing`, { method: "POST" }); loadDetail(); };
    el.querySelectorAll("[data-sc]").forEach((b) => b.onclick = async () => {
      const kind = b.dataset.sc;
      if (kind === "clear") await fetch(`/api/demo/scenario/${s.id}`, { method: "DELETE" });
      else await fetch("/api/demo/scenario", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ site_id: s.id, kind, minutes: 30 }) });
      loadDetail();
    });
  }

  const metric = (k, v) => `<div class="metric"><span>${k}</span><b>${esc(v)}</b></div>`;
  function alertHtml(a) {
    const cd = a.kind === "lightning" ? `<div class="countdown" data-until="${esc(a.data.all_clear_at)}">${fmtCountdown(a.data.all_clear_at)}</div><div class="small muted">until all-clear · ${a.data.strike_count} strikes, nearest ${a.data.nearest_mi} mi</div>` : "";
    return `<div class="alert ${a.level}"><div class="t">${esc(a.title)}</div>${cd}<p>${esc(a.message)}</p></div>`;
  }
  function reportHtml(r) {
    return `<div class="report">${r.photo_url ? `<img src="${esc(r.photo_url)}" alt="">` : ""}<div>
      <b>${REPORT_ICONS[r.hazard_type] || ""} ${esc(r.hazard_label)}</b> · sev ${r.severity}${r.distance_mi != null ? ` · ${r.distance_mi} mi` : ""}<br>
      ${esc(r.description)}<br><span class="muted">${esc(r.reporter || "crew")} · ${new Date(r.created_at).toLocaleString()}</span></div></div>`;
  }

  setInterval(() => document.querySelectorAll(".countdown[data-until]").forEach((el) => { el.textContent = fmtCountdown(el.dataset.until); }), 1000);

  // ---------- toasts ----------
  function toast(level, title, body, siteId) {
    const t = document.createElement("div");
    t.className = `toast ${level}`;
    t.innerHTML = `<b>${esc(title)}</b><div class="small">${esc(body)}</div>`;
    t.onclick = () => { if (siteId) select(siteId, true); t.remove(); };
    $("#toasts").prepend(t);
    setTimeout(() => t.remove(), level === "stop-work" ? 20000 : 9000);
    while ($("#toasts").children.length > 3) $("#toasts").lastChild.remove();
  }
  function notifyBrowser(title, body) {
    if ("Notification" in window && Notification.permission === "granted") new Notification(title, { body });
  }

  // ---------- realtime ----------
  function connect() {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => $("#conn").classList.add("on");
    ws.onclose = () => { $("#conn").classList.remove("on"); setTimeout(connect, 3000); };
    ws.onmessage = (ev) => {
      const { type, data } = JSON.parse(ev.data);
      if (type === "site_update") {
        upsertSite(data);
        if (data.id === state.selected) debounceDetail();
      } else if (type === "alert") {
        toast(data.level, `${data.site_name}: ${data.title}`, data.message, data.site_id);
        if (data.level === "stop-work" || data.level === "danger") notifyBrowser(`${data.site_name}: ${data.title}`, data.message);
        if (data.site_id === state.selected) debounceDetail();
      } else if (type === "alert_updated" || type === "alert_cleared") {
        if (type === "alert_cleared" && data.kind === "lightning") toast("clear", `${data.site_name}: lightning all-clear`, "30 minutes without a strike within 10 mi. Work may resume.", data.site_id);
        if (data.site_id === state.selected) debounceDetail();
      } else if (type === "strikes") {
        for (const s of data.strikes) {
          const c = L.circleMarker([s.lat, s.lon], { radius: 4, color: "#f2b705", fillOpacity: 0.9 }).addTo(strikeLayer);
          setTimeout(() => strikeLayer.removeLayer(c), 60000);
        }
      } else if (type === "report") {
        addReportMarker(data);
        toast("warning", `Crew report: ${data.hazard_label}`, data.description || "", data.site_id);
        if (data.site_id === state.selected) debounceDetail();
      } else if (type === "briefing") {
        if ($("#view-briefings").classList.contains("active")) loadBriefings();
      }
    };
  }
  let detailTimer;
  const debounceDetail = () => { clearTimeout(detailTimer); detailTimer = setTimeout(loadDetail, 400); };

  // ---------- report dialog ----------
  const dlg = $("#report-dialog"), form = $("#report-form");
  async function loadTypes() {
    const types = await (await fetch("/api/reports/types")).json();
    $("#hazard-type").innerHTML = Object.entries(types).map(([k, v]) => `<option value="${k}">${REPORT_ICONS[k] || ""} ${esc(v)}</option>`).join("");
  }
  function setPin(lat, lon) {
    form.lat.value = lat.toFixed(5); form.lon.value = lon.toFixed(5);
    if (state.pin) state.pin.setLatLng([lat, lon]); else state.pin = L.marker([lat, lon], { draggable: true }).addTo(map).on("dragend", (e) => setPin(e.target.getLatLng().lat, e.target.getLatLng().lng));
  }
  function openReport(lat, lon, siteId) {
    form.reset(); $("#sev-out").textContent = "3"; $("#report-error").textContent = "";
    form.site_id.value = siteId || "";
    if (lat != null) setPin(lat, lon);
    dlg.show();
  }
  $("#report-btn").onclick = () => {
    const s = state.sites.find((x) => x.id === state.selected);
    const c = map.getCenter();
    openReport(s ? s.lat + 0.004 : c.lat, s ? s.lon + 0.004 : c.lng, s?.id);
  };
  map.on("click", (e) => { if (dlg.open) { setPin(e.latlng.lat, e.latlng.lng); form.site_id.value = ""; } });
  $("#sev").oninput = (e) => { $("#sev-out").textContent = e.target.value; };
  $("#gps-btn").onclick = () => navigator.geolocation?.getCurrentPosition(
    (p) => { setPin(p.coords.latitude, p.coords.longitude); form.site_id.value = ""; map.setView([p.coords.latitude, p.coords.longitude], 14); },
    (err) => { $("#report-error").textContent = `GPS unavailable: ${err.message}`; });
  const closeReport = () => { dlg.close(); if (state.pin) { map.removeLayer(state.pin); state.pin = null; } };
  $("#report-cancel").onclick = closeReport;
  form.onsubmit = async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    if (!fd.get("photo")?.size) fd.delete("photo");
    if (!fd.get("site_id")) fd.delete("site_id");
    $("#report-submit").disabled = true;
    try {
      const r = await fetch("/api/reports", { method: "POST", body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      closeReport();
    } catch (err) {
      $("#report-error").textContent = err.message;
    } finally {
      $("#report-submit").disabled = false;
    }
  };

  // ---------- other views ----------
  async function loadBriefings() {
    const list = await (await fetch("/api/briefings")).json();
    $("#briefing-list").innerHTML = list.length ? list.map((b) => `<div class="card">
      <span class="chip ${b.level}">${b.hazard_index}</span> <b>${esc(b.site_name)}</b> · ${esc(b.for_date)}
      <pre>${esc(b.text.split("\n").slice(2).join("\n"))}</pre></div>`).join("")
      : `<p class="muted">No briefings yet. They go out at 6:00 PM local time, or generate one from a site's panel.</p>`;
  }
  async function loadReports() {
    const list = await (await fetch("/api/reports")).json();
    $("#report-list").innerHTML = list.length ? list.map((r) => `<div class="card">${reportHtml(r).replace('class="report"', "")}
      <div class="small muted">${r.lat.toFixed(4)}, ${r.lon.toFixed(4)}${r.site_id ? ` · ${esc(r.site_id)}` : ""}</div></div>`).join("")
      : `<p class="muted">No hazard reports yet.</p>`;
  }
  document.querySelectorAll(".tab").forEach((t) => t.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${t.dataset.view}`));
    if (t.dataset.view === "briefings") loadBriefings();
    if (t.dataset.view === "reports") loadReports();
    if (t.dataset.view === "dashboard") setTimeout(() => map.invalidateSize(), 50);
  });

  // ---------- boot ----------
  if ("Notification" in window && Notification.permission === "default") {
    document.addEventListener("click", () => Notification.requestPermission(), { once: true });
  }
  loadTypes();
  loadSites();
  fetch("/api/reports").then((r) => r.json()).then((rs) => rs.forEach(addReportMarker));
  setInterval(loadSites, 60000);
  connect();
})();
