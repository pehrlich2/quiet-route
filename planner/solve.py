"""Build a bi-weekly route plan: sample customers from real addresses, split them into
service days, and solve each day as a multi-trip route with Williston dump runs.

Each day is modelled as a capacitated vehicle routing problem where every "vehicle"
is one trailer load: the first load leaves the yard, every load ends at the dump,
and later loads start from the dump. Solving them together lets OR-Tools decide
which stops go in which load. The day ends with a drive from the dump back to the yard.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

M_PER_MI = 1609.344
# Williston facilities, from E911 address points.
TRANSFER_STATION = (44.46407, -73.12804)   # Casella transfer station, 262 Avenue B (trash)
RECYCLING_FACILITY = (44.46537, -73.12749)  # CSWD recycling facility (MRF), 357 Avenue C; Mon–Fri 6–3
COMPOST_SITE = (44.47358, -73.08149)        # Green Mountain Compost, 1042 Redmond Rd
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri")
DAY_LABELS = [f"Wk {w} {d}" for w in ("A", "B") for d in WEEKDAYS]

DEFAULTS = {
    "customers": 300,
    "towns": ["Jericho", "Essex Town", "Hinesburg", "Richmond"],
    "seed": 1,
    "adoption": "uniform",   # "uniform" or "clustered" (neighbors sign up near each other)
    "assign": "town",        # "town" (days split by town) or "sweep" (pie slices around the dump)
    "service_days": 10,      # every-other-week service over two 5-day weeks
    "lbs_per_stop": 60,
    "load_lbs": 3500,        # trailer fills by volume: ~14.5 yd³ of loose trash at ~225–300 lb/yd³
    "stop_min": 1.5,         # minutes at each stop, including the tipper cycle
    "dump_min": 20,          # minutes at the transfer station per load
    "yard": list(TRANSFER_STATION),
    "dump": list(TRANSFER_STATION),
    "recycle_dump": list(RECYCLING_FACILITY),
    "compost_site": list(COMPOST_SITE),
    "solve_seconds": 2.0,    # per service day
    # Recycling: "trash" (trash only), "alternate" (trash one week, recycling the next,
    # same weekday, same trailer), "sameday" (both carts out the same day, two passes,
    # same trailer), or "both" (both carts in one visit, split trailer).
    "recycling": "sameday",
    "recycle_lbs": 20,        # recycling per pickup; bulky but light
    "recycle_load_lbs": 1800, # same box full of loose recycling (~100–150 lb/yd³)
    "split_trash_pct": 60,    # split trailer: trash side; 60% balances 60 lb trash vs 20 lb recycling
    "both_stop_factor": 1.6,  # a two-cart stop takes ~1.6x a one-cart stop
    "mrf_min": 15,            # extra minutes per load to also tip at the recycling facility
}


# ---------- data ----------

@lru_cache(maxsize=1)
def load_addresses() -> list[dict]:
    return json.loads((DATA / "addresses.json").read_text())


@lru_cache(maxsize=1)
def load_roads():
    z = np.load(DATA / "roads.npz")
    n = len(z["node_lat"])
    g_len = csr_matrix((z["length_m"], (z["src"], z["dst"])), shape=(n, n))
    g_time = csr_matrix((z["time_s"], (z["src"], z["dst"])), shape=(n, n))
    lat, lon = z["node_lat"], z["node_lon"]
    lat0 = math.radians(float(lat.mean()))
    tree = cKDTree(np.column_stack([lon * math.cos(lat0), lat]))
    return {"lat": lat, "lon": lon, "len": g_len, "time": g_time, "tree": tree, "cos": math.cos(lat0)}


def snap(roads, lat: float, lon: float) -> int:
    _, i = roads["tree"].query([lon * roads["cos"], lat])
    return int(i)


# ---------- customers ----------

def sample_customers(cfg: dict) -> list[dict]:
    rng = np.random.default_rng(cfg["seed"])
    pool = [a for a in load_addresses() if a["town"] in cfg["towns"]]
    if not pool:
        return []
    n = min(cfg["customers"], len(pool))
    # Multi-unit buildings are more likely to contain a customer, capped so one complex doesn't dominate.
    w = np.array([min(a["units"], 4) for a in pool], dtype=float)

    if cfg["adoption"] == "clustered":
        lat = np.array([a["lat"] for a in pool])
        lon = np.array([a["lon"] for a in pool]) * math.cos(math.radians(44.4))
        n_seeds = max(3, n // 20)
        seeds = rng.choice(len(pool), size=n_seeds, replace=False, p=w / w.sum())
        # Each seed spreads by word of mouth: weight falls off over ~1 km.
        sigma = 0.009
        near = np.zeros(len(pool))
        for s in seeds:
            d2 = (lat - lat[s]) ** 2 + (lon - lon[s]) ** 2
            near += np.exp(-d2 / (2 * sigma ** 2))
        w = w * (0.15 + near)

    idx = rng.choice(len(pool), size=n, replace=False, p=w / w.sum())
    return [pool[i] for i in idx]


# ---------- day assignment ----------

def _sweep(points: list[dict], center: tuple[float, float], k: int) -> list[list[dict]]:
    """Sort by compass angle around center and cut into k equal slices."""
    if k <= 0 or not points:
        return []
    ang = sorted(points, key=lambda p: math.atan2(p["lat"] - center[0], (p["lon"] - center[1]) * 0.71))
    size = math.ceil(len(ang) / k)
    return [ang[i:i + size] for i in range(0, len(ang), size)]


def assign_days(customers: list[dict], cfg: dict) -> list[list[dict]]:
    days_n = cfg["service_days"]
    dump = tuple(cfg["dump"])
    by_town: dict[str, list[dict]] = {}
    for c in customers:
        by_town.setdefault(c["town"], []).append(c)

    if cfg["assign"] != "town" or len(by_town) > days_n:
        return _sweep(customers, dump, days_n)

    # Largest-remainder allocation of days to towns, at least one day per town.
    total = len(customers)
    raw = {t: len(cs) / total * days_n for t, cs in by_town.items()}
    alloc = {t: max(1, math.floor(r)) for t, r in raw.items()}
    while sum(alloc.values()) < days_n:
        t = max(raw, key=lambda t: raw[t] - alloc[t])
        alloc[t] += 1
    while sum(alloc.values()) > days_n:
        t = max((t for t in alloc if alloc[t] > 1), key=lambda t: alloc[t] - raw[t])
        alloc[t] -= 1

    days = []
    for t in sorted(by_town):
        cs = by_town[t]
        centroid = (sum(c["lat"] for c in cs) / len(cs), sum(c["lon"] for c in cs) / len(cs))
        days.extend(_sweep(cs, centroid, alloc[t]))
    return days


# ---------- routing ----------

def _path(pred_row: np.ndarray, src: int, dst: int) -> list[int]:
    out = [dst]
    while out[-1] != src:
        p = pred_row[out[-1]]
        if p < 0:
            return [src, dst]
        out.append(int(p))
    return out[::-1]


def solve_day(stops: list[dict], cfg: dict, stream: str = "trash", from_dump: bool = False) -> dict:
    """Solve one pass over `stops`. from_dump=True starts the pass at the dump (a second
    pass on the same day, right after the first pass's last dump run)."""
    roads = load_roads()
    yard_n = snap(roads, *cfg["yard"])
    dump_n = snap(roads, *(cfg["recycle_dump"] if stream == "recycling" else cfg["dump"]))
    stop_n = [snap(roads, s["lat"], s["lon"]) for s in stops]
    # Routing nodes: 0 = yard, 1 = dump, 2.. = stops.
    gnode = [yard_n, dump_n] + stop_n
    uniq = sorted(set(gnode))
    row = {g: i for i, g in enumerate(uniq)}
    dist, pred = dijkstra(roads["len"], directed=True, indices=uniq, return_predecessors=True)
    tdist = dijkstra(roads["time"], directed=True, indices=uniq)

    n = len(gnode)
    M = [[int(dist[row[gnode[a]], gnode[b]]) for b in range(n)] for a in range(n)]
    T = [[float(tdist[row[gnode[a]], gnode[b]]) for b in range(n)] for a in range(n)]

    # One capacity dimension per compartment: (lbs per stop, lbs per load).
    if stream == "recycling":
        dims = [(int(cfg["recycle_lbs"]), int(cfg["recycle_load_lbs"]))]
    elif stream == "both":
        frac = cfg["split_trash_pct"] / 100
        dims = [(int(cfg["lbs_per_stop"]), int(cfg["load_lbs"] * frac)),
                (int(cfg["recycle_lbs"]), int(cfg["recycle_load_lbs"] * (1 - frac)))]
    else:
        dims = [(int(cfg["lbs_per_stop"]), int(cfg["load_lbs"]))]
    stops_per_load = min(cap // lbs for lbs, cap in dims)
    stop_min = cfg["stop_min"] * (cfg["both_stop_factor"] if stream == "both" else 1)
    dump_min = cfg["dump_min"] + (cfg["mrf_min"] if stream == "both" else 0)
    loads_needed = max(math.ceil(len(stops) * lbs / cap) for lbs, cap in dims) if stops else 0
    V = max(1, loads_needed + 1)
    starts = [1] * V if from_dump else [0] + [1] * (V - 1)
    ends = [1] * V
    manager = pywrapcp.RoutingIndexManager(n, V, starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    def dist_cb(i, j):
        return M[manager.IndexToNode(i)][manager.IndexToNode(j)]

    arc = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(arc)
    for k, (lbs, cap) in enumerate(dims):
        demand = [0, 0] + [lbs] * len(stops)
        dem = routing.RegisterUnaryTransitCallback(lambda i, d=demand: d[manager.IndexToNode(i)])
        routing.AddDimensionWithVehicleCapacity(dem, 0, [cap] * V, True, f"Load{k}")
    # An extra dump run costs time at the station; price it like ~5 km of driving.
    routing.SetFixedCostOfAllVehicles(5000)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(int(cfg["solve_seconds"] * 1000))
    sol = routing.SolveWithParameters(params)
    if sol is None:
        raise RuntimeError("no route found")

    def leg_coords(a: int, b: int) -> list[list[float]]:
        nodes = _path(pred[row[gnode[a]]], gnode[a], gnode[b])
        return [[round(float(roads["lat"][x]), 5), round(float(roads["lon"][x]), 5)] for x in nodes]

    trips = []
    for v in range(V):
        idx = routing.Start(v)
        seq = [manager.IndexToNode(idx)]
        while not routing.IsEnd(idx):
            idx = sol.Value(routing.NextVar(idx))
            seq.append(manager.IndexToNode(idx))
        visits = [x for x in seq if x >= 2]
        if not visits:
            # An empty first load just means the day starts by driving yard -> dump; fold it into later loads.
            continue
        legs = list(zip(seq, seq[1:]))
        meters = sum(M[a][b] for a, b in legs)
        # Collecting = between first and last stop; the rest is driving to/from yard or dump.
        collect = sum(M[a][b] for a, b in legs if a >= 2 and b >= 2)
        coords: list[list[float]] = []
        for a, b in legs:
            seg = leg_coords(a, b)
            coords.extend(seg if not coords else seg[1:])
        trips.append({
            "stops": [x - 2 for x in visits],
            "lbs": len(visits) * sum(l for l, _ in dims),
            "meters": meters,
            "collect_meters": collect,
            "drive_s": sum(T[a][b] for a, b in legs),
            "coords": coords,
        })

    # The solver always starts load 1 at the yard; if it left that load empty, the truck
    # still has to get from the yard to the dump before starting from there.
    first_from_yard = from_dump or manager.IndexToNode(sol.Value(routing.NextVar(routing.Start(0)))) >= 2
    out_m = 0 if first_from_yard else M[0][1]
    out_s = 0.0 if first_from_yard else T[0][1]
    home_m = M[1][0]
    home_s = T[1][0]
    home = leg_coords(1, 0)
    meters = sum(t["meters"] for t in trips) + home_m + out_m
    drive_s = sum(t["drive_s"] for t in trips) + home_s + out_s
    service_min = len(stops) * stop_min + len(trips) * dump_min
    day = {
        "stops": [{"lat": s["lat"], "lon": s["lon"], "addr": s["addr"], "town": s["town"]} for s in stops],
        "trips": trips,
        "start_coords": [] if first_from_yard else leg_coords(0, 1),
        "home_coords": home,
        "miles": meters / M_PER_MI,
        "collect_miles": sum(t["collect_meters"] for t in trips) / M_PER_MI,
        "dump_runs": len(trips),
        "tons": len(stops) * sum(l for l, _ in dims) / 2000,
        "stream": stream,
        "stops_per_load": stops_per_load,
        "stop_min": stop_min,
        "dump_min": dump_min,
        "drive_min": drive_s / 60,
        "service_min": service_min,
        "total_min": drive_s / 60 + service_min,
        "home_miles": home_m / M_PER_MI,
        "home_min": home_s / 60,
    }
    # One entry per pass over the stops; a same-day trash + recycling day has two.
    day["passes"] = [{k: day[k] for k in ("miles", "collect_miles", "drive_min", "dump_runs",
                                           "stops_per_load", "stop_min", "dump_min")}]
    return day


def merge_passes(first: dict, second: dict) -> dict:
    """Join two passes over the same stops into one day: the truck goes from the first
    pass's last dump straight into the second pass instead of heading home."""
    day = dict(second)
    day["stream"] = "sameday"
    day["trips"] = first["trips"] + second["trips"]
    day["start_coords"] = first["start_coords"]
    for k in ("collect_miles", "dump_runs", "tons", "service_min"):
        day[k] = first[k] + second[k]
    day["miles"] = first["miles"] - first["home_miles"] + second["miles"]
    day["drive_min"] = first["drive_min"] - first["home_min"] + second["drive_min"]
    day["total_min"] = first["total_min"] - first["home_min"] + second["total_min"]
    day["passes"] = first["passes"] + second["passes"]
    return day


def solve(overrides: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(overrides or {})}
    customers = sample_customers(cfg)
    days = []
    if cfg["recycling"] == "alternate":
        # Each customer keeps one weekday: trash in week A, recycling in week B.
        groups = assign_days(customers, {**cfg, "service_days": cfg["service_days"] // 2})
        week_b = []
        for i, g in enumerate(groups):
            wd = WEEKDAYS[i % len(WEEKDAYS)]
            towns = sorted({s["town"] for s in g})
            t = solve_day(g, cfg, "trash")
            t.update(label=f"Wk A {wd}", towns=towns)
            r = solve_day(g, cfg, "recycling")
            r.update(label=f"Wk B {wd}", towns=towns)
            days.append(t)
            week_b.append(r)
        days.extend(week_b)
        visits = 2
    elif cfg["recycling"] == "sameday":
        # Both carts out on the same day: a trash pass, then a recycling pass from the dump.
        for i, g in enumerate(assign_days(customers, cfg)):
            d = merge_passes(solve_day(g, cfg, "trash"), solve_day(g, cfg, "recycling", from_dump=True))
            d.update(label=DAY_LABELS[i] if i < len(DAY_LABELS) else f"Day {i + 1}",
                     towns=sorted({s["town"] for s in g}))
            days.append(d)
        visits = 1
    else:
        stream = "both" if cfg["recycling"] == "both" else "trash"
        for i, g in enumerate(assign_days(customers, cfg)):
            d = solve_day(g, cfg, stream)
            d.update(label=DAY_LABELS[i] if i < len(DAY_LABELS) else f"Day {i + 1}",
                     towns=sorted({s["town"] for s in g}))
            days.append(d)
        visits = 1
    return {"config": cfg, "customers": len(customers), "visits_per_customer": visits, "days": days}


if __name__ == "__main__":
    import sys
    import time

    t0 = time.time()
    overrides = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    plan = solve(overrides)
    out = ROOT / "out" / "plan.json"
    out.write_text(json.dumps(plan, separators=(",", ":")))
    print(f"{plan['customers']} customers, {len(plan['days'])} days, solved in {time.time() - t0:.1f}s -> {out}")
    for d in plan["days"]:
        print(f"  {d['label']:11} {d['stream']:9} {', '.join(d['towns']):28} {len(d['stops']):4} stops "
              f"{d['miles']:6.1f} mi  {d['dump_runs']} loads  {d['total_min'] / 60:4.1f} h")
