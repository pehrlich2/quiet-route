const COLORS = ["#1b7f5e", "#d1495b", "#2f6fb2", "#e0a100", "#7b4fb3", "#1a9fb0", "#c2562c", "#5f8f1f", "#b83f8f", "#44546a"];
const $ = (id) => document.getElementById(id);
const PLAN_FIELDS = ["customers", "adoption", "assign", "lbs_per_stop", "load_lbs", "stop_min", "dump_min", "seed"];
const ENERGY_FIELDS = ["battery", "reserve", "kwhmi", "winterPen", "heatKw", "tipKwh", "workHrs"];

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
  return v;
}

function dayEnergy(d, e) {
  const heat = e.winter ? e.heatKw * (d.total_min / 60) : 0;
  return d.miles * e.driveKwhMi + d.stops.length * e.tipKwh + heat;
}

// How many stops this day's pattern could reach before running out of hours or battery.
function dayCapacity(d, e, cfg) {
  const n = d.stops.length;
  if (!n) return { time: 0, battery: 0 };
  const transitMi = d.miles - d.collect_miles;
  const transitPerLoad = transitMi / d.dump_runs;
  const transitMinPerLoad = (d.drive_min * transitMi) / d.miles / d.dump_runs;
  const collectMinPerStop = (d.drive_min * d.collect_miles) / d.miles / n;
  const collectMiPerStop = d.collect_miles / n;
  const perLoadStops = Math.max(1, Math.floor(cfg.load_lbs / cfg.lbs_per_stop));
  const work = e.workHrs * 60;
  const fits = (s, kind) => {
    const loads = Math.ceil(s / perLoadStops);
    const minutes = s * (cfg.stop_min + collectMinPerStop) + loads * (cfg.dump_min + transitMinPerLoad);
    if (kind === "time") return minutes <= work;
    const heat = e.winter ? e.heatKw * (minutes / 60) : 0;
    const kwh = (s * collectMiPerStop + loads * transitPerLoad) * e.driveKwhMi + s * e.tipKwh + heat;
    return kwh <= e.usable;
  };
  const search = (kind) => { let s = 0; while (s < 2000 && fits(s + 1, kind)) s++; return s; };
  return { time: search("time"), battery: search("battery") };
}

const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : 0; };
const pctClass = (p) => (p > 1 ? "bad" : p > 0.85 ? "warn" : "ok");

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
  const dump = plan.config.dump;
  L.circleMarker(dump, { radius: 9, color: "#15211D", weight: 2, fillColor: "#e0a100", fillOpacity: 1 })
    .bindTooltip("Williston transfer station (dump)").addTo(routeLayer);
  bounds.push(dump);
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
    tr.innerHTML = `<td class="day"><span class="sw" style="background:${COLORS[i % COLORS.length]}"></span>${d.label}<small>${d.towns.join(", ")}</small></td>
      <td class="n">${d.stops.length}</td><td class="n">${d.miles.toFixed(1)}</td><td class="n">${d.dump_runs}</td>
      <td class="n">${(d.total_min / 60).toFixed(1)}</td><td class="n"><span class="pct ${pctClass(p)}">${Math.round(p * 100)}%</span></td>`;
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
    tile("Stops per day", Math.round(plan.customers / days.length), "average") +
    tile("Miles per 2 weeks", Math.round(totalMi).toLocaleString(), `${(totalMi / days.length).toFixed(0)} per day avg`) +
    tile("Hardest day battery", `<span class="pct ${pctClass(worst)}">${Math.round(worst * 100)}%</span>`, `of usable, ${e.winter ? "winter" : "summer"}`) +
    tile("Max stops / day", limit.toLocaleString(), capTime <= capBatt ? "limited by hours" : "limited by battery") +
    tile("Max customers", (limit * days.length).toLocaleString(), "at this density, every other week");
  $("capNote").textContent =
    `Max stops is an estimate: it scales each day's actual stop spacing and Williston runs up until the ${e.workHrs}-hour day ` +
    `(${capTime} stops) or the ${Math.round(e.usable)} kWh usable battery (${capBatt} stops) runs out. Median across days.`;
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
$("showAll").addEventListener("click", () => { focus = null; renderAll(false); });

(async function init() {
  setStatus("Loading plan…");
  const [towns, p, addr] = await Promise.all([
    fetch("/api/towns").then((r) => r.json()),
    fetch("/api/plan").then((r) => r.json()),
    fetch("/data/addresses.json").then((r) => r.json()),
  ]);
  addresses = addr;
  plan = p;
  fillForm(plan.config, towns.towns);
  renderAll();
  setStatus("");
})();
