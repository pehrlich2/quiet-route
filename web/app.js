const COLORS = ["#1b7f5e", "#d1495b", "#2f6fb2", "#e0a100", "#7b4fb3", "#1a9fb0", "#c2562c", "#5f8f1f", "#b83f8f", "#44546a"];
const $ = (id) => document.getElementById(id);
const PLAN_FIELDS = ["customers", "adoption", "assign", "recycling", "lbs_per_stop", "load_lbs", "recycle_lbs", "recycle_load_lbs", "split_trash_pct", "stop_min", "dump_min", "seed"];
const STREAM_LABEL = { trash: "trash", recycling: "recycling", both: "trash + recycling, split trailer", sameday: "trash, then recycling" };
const ENERGY_FIELDS = ["battery", "reserve", "kwhmi", "winterPen", "heatKw", "tipKwh", "workHrs", "charges", "chargeMin", "chargeKw"];

let plan = null;
let focus = null; // index of the highlighted day, or null for all
let addresses = [];

const map = L.map("map", { preferCanvas: true }).setView([44.45, -73.02], 11);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "© OpenStreetMap contributors", maxZoom: 19,
}).addTo(map);
const homesLayer = L.layerGroup().addTo(map);
const routeLayer = L.layerGroup().addTo(map);

// ---------- energy model ----------

function energyInputs() {
  const v = {};
  ENERGY_FIELDS.forEach((k) => (v[k] = parseFloat($(k).value) || 0));
  v.winter = document.querySelector("input[name=season]:checked").value === "winter";
  v.usable = v.battery * (1 - v.reserve / 100);
  v.driveKwhMi = v.kwhmi * (v.winter ? 1 + v.winterPen / 100 : 1);
  // Energy a midday fast charge adds: charger power x time, ~90% efficient, capped at a full battery.
  v.midday = v.charges > 1 ? Math.min(v.usable, (v.chargeKw * v.chargeMin) / 60 * 0.9) : 0;
  v.allowed = (v.usable + v.midday) / v.usable; // day energy limit, as a share of one charge
  return v;
}

const passesOf = (d) => d.passes || [d];

function dayEnergy(d, e) {
  const heat = e.winter ? e.heatKw * (d.total_min / 60) : 0;
  return d.miles * e.driveKwhMi + d.stops.length * passesOf(d).length * e.tipKwh + heat;
}

// Charges this day needs (1 = overnight only), capped at what the settings allow.
const needsMidday = (kwh, e) => e.charges > 1 && kwh > e.usable;
const dayMinutes = (d, e) => d.total_min + (needsMidday(dayEnergy(d, e), e) ? e.chargeMin : 0);

// How many houses this day's pattern could serve before running out of hours or battery.
// A day can have several passes over the same houses (trash, then recycling).
function dayCapacity(d, e, cfg) {
  const n = d.stops.length;
  if (!n) return { time: 0, battery: 0 };
  const passes = passesOf(d).map((p) => {
    const transitMi = p.miles - p.collect_miles;
    return {
      perLoad: Math.max(1, p.stops_per_load ?? Math.floor(cfg.load_lbs / cfg.lbs_per_stop)),
      stopMin: p.stop_min ?? cfg.stop_min,
      dumpMin: p.dump_min ?? cfg.dump_min,
      collectMiPerStop: p.collect_miles / n,
      collectMinPerStop: (p.drive_min * p.collect_miles) / p.miles / n,
      transitMiPerLoad: transitMi / p.dump_runs,
      transitMinPerLoad: (p.drive_min * transitMi) / p.miles / p.dump_runs,
    };
  });
  const cost = (s) => {
    let minutes = 0, miles = 0;
    for (const p of passes) {
      const loads = Math.ceil(s / p.perLoad);
      minutes += s * (p.stopMin + p.collectMinPerStop) + loads * (p.dumpMin + p.transitMinPerLoad);
      miles += s * p.collectMiPerStop + loads * p.transitMiPerLoad;
    }
    const heat = e.winter ? e.heatKw * (minutes / 60) : 0;
    const kwh = miles * e.driveKwhMi + s * passes.length * e.tipKwh + heat;
    return { minutes: minutes + (needsMidday(kwh, e) ? e.chargeMin : 0), kwh };
  };
  const work = e.workHrs * 60;
  const fits = (s, kind) => {
    const c = cost(s);
    return kind === "time" ? c.minutes <= work : c.kwh <= e.usable + e.midday;
  };
  const search = (kind) => { let s = 0; while (s < 3000 && fits(s + 1, kind)) s++; return s; };
  return { time: search("time"), battery: search("battery") };
}

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : 0; };
// p is energy as a share of one charge; allowed = charges per day in the settings.
const pctClass = (p, allowed = 1) => (p > allowed ? "bad" : p > 0.85 ? "warn" : "ok");

// ---------- rendering ----------

function renderHomes() {
  homesLayer.clearLayers();
  if (!$("showHomes").checked || !plan) return;
  const towns = new Set(plan.config.towns);
  addresses.filter((a) => towns.has(a.town)).forEach((a) =>
    L.circleMarker([a.lat, a.lon], { radius: 1.5, stroke: false, fillColor: "#8a9a94", fillOpacity: 0.45, interactive: false }).addTo(homesLayer));
}

function renderMap() {
  routeLayer.clearLayers();
  if (!plan) return;
  const bounds = [];
  plan.days.forEach((d, i) => {
    if (focus !== null && focus !== i) return;
    const color = COLORS[i % COLORS.length];
    const weight = focus === null ? 2.5 : 4;
    d.trips.forEach((t) => L.polyline(t.coords, { color, weight, opacity: 0.85 }).addTo(routeLayer));
    if (d.home_coords.length) L.polyline(d.home_coords, { color, weight, opacity: 0.4, dashArray: "4 6" }).addTo(routeLayer);
    if (d.start_coords.length) L.polyline(d.start_coords, { color, weight, opacity: 0.4, dashArray: "4 6" }).addTo(routeLayer);
    d.trips.forEach((t, ti) => t.stops.forEach((si, k) => {
      const s = d.stops[si];
      bounds.push([s.lat, s.lon]);
      L.circleMarker([s.lat, s.lon], { radius: focus === null ? 4 : 6, color: "#fff", weight: 1, fillColor: color, fillOpacity: 1 })
        .bindTooltip(`${d.label} · load ${ti + 1}, stop ${k + 1}<br>${s.addr}, ${s.town}`)
        .addTo(routeLayer);
    }));
  });
  const c = plan.config;
  const facility = (pt, label, fill) => pt && L.circleMarker(pt, { radius: 8, color: "#15211D", weight: 2, fillColor: fill, fillOpacity: 1 })
    .bindTooltip(label).addTo(routeLayer);
  facility(c.dump, "Casella transfer station (trash), 262 Avenue B<br>Mon–Fri 7:30–4, Sat 7–1", "#e0a100");
  facility(c.recycle_dump, "CSWD recycling facility, 357 Avenue C<br>Mon–Fri 6–3", "#2f6fb2");
  facility(c.compost_site, "Green Mountain Compost, 1042 Redmond Rd<br>(compost routes not modeled yet)", "#5f8f1f");
  bounds.push(c.dump);
  if (bounds.length > 1) map.fitBounds(bounds, { padding: [30, 30] });
}

function renderTable() {
  const e = energyInputs();
  const tbody = $("days").querySelector("tbody");
  tbody.innerHTML = "";
  plan.days.forEach((d, i) => {
    const p = dayEnergy(d, e) / e.usable;
    const tr = document.createElement("tr");
    if (focus === i) tr.className = "sel";
    tr.innerHTML = `<td class="day"><span class="sw" style="background:${COLORS[i % COLORS.length]}"></span>${d.label}<small>${d.towns.join(", ")} · ${STREAM_LABEL[d.stream] || "trash"}</small></td>
      <td class="n">${d.stops.length}</td><td class="n">${d.miles.toFixed(1)}</td><td class="n">${d.dump_runs}</td>
      <td class="n">${(dayMinutes(d, e) / 60).toFixed(1)}</td><td class="n"><span class="pct ${pctClass(p, e.allowed)}" title="${Math.round(dayEnergy(d, e))} kWh; one charge = ${Math.round(e.usable)} kWh usable${p > 1 ? (p <= e.allowed ? `. Needs the midday charge (+${Math.round(e.midday)} kWh, included in hours).` : ". More than the battery plus any midday charge can supply.") : ""}">${Math.round(p * 100)}%</span></td>`;
    tr.addEventListener("click", () => { focus = focus === i ? null : i; renderAll(false); });
    tbody.appendChild(tr);
  });
}

function renderSummary() {
  const e = energyInputs();
  const cfg = plan.config;
  const days = plan.days.filter((d) => d.stops.length);
  const totalMi = days.reduce((a, d) => a + d.miles, 0);
  const worst = Math.max(...days.map((d) => dayEnergy(d, e) / e.usable));
  const caps = days.map((d) => dayCapacity(d, e, cfg));
  const capTime = median(caps.map((c) => c.time));
  const capBatt = median(caps.map((c) => c.battery));
  const limit = Math.min(capTime, capBatt);
  const tile = (label, value, sub = "") => `<div class="tile"><span>${label}</span><b>${value}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
  $("tiles").innerHTML =
    tile("Customers", plan.customers.toLocaleString(), `${days.length} service days`) +
    tile("Stops per day", Math.round(days.reduce((a, d) => a + d.stops.length, 0) / days.length), "average") +
    tile("Miles per 2 weeks", Math.round(totalMi).toLocaleString(), `${(totalMi / days.length).toFixed(0)} per day avg`) +
    tile("Hardest day battery", `<span class="pct ${pctClass(worst, e.allowed)}">${Math.round(worst * 100)}%</span>`, `of one charge, ${e.winter ? "winter" : "summer"}${e.charges > 1 ? `, +${Math.round(e.midday)} kWh midday` : ""}`) +
    tile("Max stops / day", limit.toLocaleString(), capTime <= capBatt ? "limited by hours" : "limited by battery") +
    tile("Max customers", Math.round(limit * days.length / (plan.visits_per_customer || 1)).toLocaleString(), visitsNote(plan));
  $("capNote").textContent =
    `Max stops is an estimate: it scales each day's actual stop spacing and Williston runs up until the ${e.workHrs}-hour day ` +
    `(${capTime} houses) or the battery runs out: ${e.charges > 1 ? `${Math.round(e.usable)} kWh overnight plus ${Math.round(e.midday)} kWh from a ${e.chargeMin}-min midday charge at ${e.chargeKw} kW` : `one ${Math.round(e.usable)} kWh overnight charge`} (${capBatt} houses). Median across days.`;
}

function visitsNote(p) {
  const m = p.config.recycling;
  if (m === "alternate") return "trash + alternate-week recycling";
  if (m === "both") return "both carts per visit, every other week";
  return "trash only, every other week";
}

function renderAll(refit = true) {
  if (!plan) return;
  renderSummary();
  renderTable();
  if (refit) renderHomes();
  renderMap();
}

// ---------- plan controls ----------

function fillForm(cfg, townCounts) {
  PLAN_FIELDS.forEach((k) => ($(k).value = cfg[k]));
  const fs = $("towns");
  fs.querySelectorAll("label").forEach((l) => l.remove());
  Object.keys(townCounts).sort().forEach((t) => {
    const id = "t_" + t.replace(/\W/g, "");
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="checkbox" id="${id}" value="${t}" ${cfg.towns.includes(t) ? "checked" : ""}> ${t} <small>${townCounts[t].toLocaleString()}</small>`;
    fs.appendChild(lab);
  });
}

function readForm() {
  const o = {};
  PLAN_FIELDS.forEach((k) => {
    const el = $(k);
    o[k] = el.tagName === "SELECT" ? el.value : parseFloat(el.value);
  });
  o.towns = [...$("towns").querySelectorAll("input:checked")].map((i) => i.value);
  return o;
}

$("planForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const body = readForm();
  if (!body.towns.length) { setStatus("Pick at least one town.", true); return; }
  $("solveBtn").disabled = true;
  const t0 = performance.now();
  setStatus(`Solving ${body.customers} customers… about 2 seconds per service day.`);
  try {
    const r = await fetch("/api/solve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
    plan = data;
    focus = null;
    renderAll();
    setStatus(`Solved in ${((performance.now() - t0) / 1000).toFixed(0)} s.`);
  } catch (err) {
    setStatus(`Couldn't solve: ${err.message}`, true);
  } finally {
    $("solveBtn").disabled = false;
  }
});

function setStatus(msg, err = false) { $("status").textContent = msg; $("status").className = "status" + (err ? " err" : ""); }

const renderNumbers = () => { if (plan) { renderSummary(); renderTable(); } };
ENERGY_FIELDS.forEach((k) => $(k).addEventListener("input", renderNumbers));
document.querySelectorAll("input[name=season]").forEach((r) => r.addEventListener("change", renderNumbers));
$("showHomes").addEventListener("change", renderHomes);

// ---------- fast chargers ----------
const chargerLayer = L.layerGroup();
function renderChargers(list) {
  chargerLayer.clearLayers();
  list.forEach((ch) => {
    const html = `<b>${ch.name}</b>${ch.operator !== ch.name ? ` · ${ch.operator}` : ""}<br>` +
      `${ch.plugs.join(", ") || "plug type unknown"}${ch.stalls ? ` · ${ch.stalls} stalls` : ""}${ch.kw ? ` · ${ch.kw} kW` : ""}` +
      `${ch.note ? `<br>${ch.note}` : ""}<br><i>Trailer fit unknown: check whether stalls are pull-through.</i>`;
    L.marker([ch.lat, ch.lon], {
      icon: L.divIcon({ className: "charger-icon", html: "⚡", iconSize: [20, 20], iconAnchor: [10, 10] }),
    }).bindTooltip(html).addTo(chargerLayer);
  });
}
$("showChargers").addEventListener("change", (ev) => (ev.target.checked ? chargerLayer.addTo(map) : chargerLayer.remove()));
$("showAll").addEventListener("click", () => { focus = null; renderAll(false); });

(async function init() {
  setStatus("Loading plan…");
  const [towns, p, addr] = await Promise.all([
    fetch("/api/towns").then((r) => r.json()),
    fetch("/api/plan").then((r) => r.json()),
    fetch("/data/addresses.json").then((r) => r.json()),
  ]);
  fetch("/data/chargers.json").then((r) => (r.ok ? r.json() : [])).then((list) => {
    renderChargers(list);
    if ($("showChargers").checked) chargerLayer.addTo(map);
  }).catch(() => {});
  addresses = addr;
  plan = p;
  fillForm(plan.config, towns.towns);
  renderAll();
  setStatus("");
})();
