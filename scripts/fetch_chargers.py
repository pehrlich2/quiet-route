"""Download DC fast chargers around the service area from OpenStreetMap (Overpass API).

Writes data/chargers.json. OSM is volunteer-mapped and can miss or mislabel stations;
check PlugShare or the DOE station locator before relying on one.
"""

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "chargers.json"
BBOX = (44.20, -73.30, 44.65, -72.75)  # south, west, north, east
QUERY = f'[out:json][timeout:90];nwr["amenity"="charging_station"]({",".join(map(str, BBOX))});out center tags;'
DC_SOCKETS = ("socket:type1_combo", "socket:chademo", "socket:nacs", "socket:tesla_supercharger", "socket:type2_combo")

# What we know that OSM doesn't record. Power figures marked "est." are typical for the
# hardware and should be confirmed on site or in PlugShare.
NOTES = {
    "Tesla Supercharger|Market Street": "250 kW (V3). Open to GM and Ford with a NACS adapter.",
    "Tesla Supercharger|Dorset Street": "150 kW (V2). V2 sites are usually Tesla-only.",
    "ChargePoint|44.4225": "Richmond, by I-89 Exit 11. 125 kW, two full stalls (confirmed by the team).",
}


def fetch() -> dict:
    data = urllib.parse.urlencode({"data": QUERY}).encode()
    req = urllib.request.Request("https://overpass-api.de/api/interpreter", data=data,
                                 headers={"User-Agent": "QuietRoutePlanner/0.1"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except Exception as e:  # Overpass is often busy; back off and retry
            print(f"  attempt {attempt + 1} failed: {e}")
            time.sleep(20)
    raise RuntimeError("Overpass unavailable")


def main() -> None:
    out = []
    for e in fetch()["elements"]:
        t = e.get("tags", {})
        if not any(s in t for s in DC_SOCKETS):
            continue
        lat = e.get("lat") or e["center"]["lat"]
        lon = e.get("lon") or e["center"]["lon"]
        operator = t.get("operator") or t.get("network") or t.get("brand") or "Unknown"
        name = t.get("name") or operator
        plugs = []
        if "socket:type1_combo" in t:
            plugs.append("CCS")
        if "socket:nacs" in t or "socket:tesla_supercharger" in t:
            plugs.append("NACS (Tesla)")
        if "socket:chademo" in t:
            plugs.append("CHAdeMO")
        kws = [float(m) for k, v in t.items() if k.endswith(":output") for m in re.findall(r"[\d.]+", v)]
        note = ""
        for key, text in NOTES.items():
            a, b = key.split("|")
            if (a in (name, operator)) and (b in str(t.get("addr:street", "")) or b in f"{lat:.4f}"):
                note = text
        # Facts confirmed on site override OSM.
        if note.startswith("Richmond"):
            kws, t = [125.0], {**t, "capacity": "2"}
        out.append({
            "name": name, "operator": operator, "lat": round(lat, 5), "lon": round(lon, 5),
            "plugs": plugs, "stalls": t.get("capacity"), "kw": max(kws) if kws else None,
            "street": t.get("addr:street"), "city": t.get("addr:city"), "note": note,
        })
    OUT.write_text(json.dumps(out, indent=1))
    print(f"wrote {len(out)} DC fast chargers to {OUT}")
    for c in out:
        print(f"  {c['name']:22} {c['operator']:14} {'/'.join(c['plugs']):22} {c['kw'] or '?':>6} kW  {c['note']}")


if __name__ == "__main__":
    main()
