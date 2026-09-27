"""Download the drivable road network around the service area from OpenStreetMap
and save it as compact arrays for fast shortest-path queries.

Writes data/roads.npz with:
  node_lat, node_lon            per-node coordinates
  src, dst, length_m, time_s    directed edges (largest strongly connected component)
"""

import json
from pathlib import Path

import numpy as np
import osmnx as ox

ROOT = Path(__file__).resolve().parent.parent
ADDR = ROOT / "data" / "addresses.json"
OUT = ROOT / "data" / "roads.npz"
PAD_DEG = 0.02  # ~2 km of road around the outermost address


def main() -> None:
    pts = json.loads(ADDR.read_text())
    lats = [p["lat"] for p in pts]
    lons = [p["lon"] for p in pts]
    # (left, bottom, right, top)
    bbox = (min(lons) - PAD_DEG, min(lats) - PAD_DEG, max(lons) + PAD_DEG, max(lats) + PAD_DEG)
    print("bbox", bbox)

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(ROOT / "data" / "osm_cache")
    G = ox.graph_from_bbox(bbox, network_type="drive", simplify=True)
    G = ox.truncate.largest_component(G, strongly=True)
    G = ox.add_edge_speeds(G)
    G = ox.add_edge_travel_times(G)
    print(f"graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    nodes = list(G.nodes)
    index = {n: i for i, n in enumerate(nodes)}
    node_lat = np.array([G.nodes[n]["y"] for n in nodes])
    node_lon = np.array([G.nodes[n]["x"] for n in nodes])

    # Keep the shortest parallel edge between each node pair.
    best: dict[tuple[int, int], tuple[float, float]] = {}
    for u, v, d in G.edges(data=True):
        key = (index[u], index[v])
        cand = (float(d["length"]), float(d["travel_time"]))
        if key not in best or cand[0] < best[key][0]:
            best[key] = cand
    src = np.array([k[0] for k in best], dtype=np.int32)
    dst = np.array([k[1] for k in best], dtype=np.int32)
    length_m = np.array([v[0] for v in best.values()])
    time_s = np.array([v[1] for v in best.values()])

    np.savez_compressed(OUT, node_lat=node_lat, node_lon=node_lon,
                        src=src, dst=dst, length_m=length_m, time_s=time_s)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
