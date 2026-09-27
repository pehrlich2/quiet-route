"""Download residential E911 address points for the service-area towns.

Source: Vermont E911 Board / VCGI "Site Structure Address Points" feature service.
Writes data/addresses.json as a compact list of {id, lat, lon, town, addr, type, units}.
"""

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SERVICE = (
    "https://services1.arcgis.com/BkFxaEFNwHqX3tAw/ArcGIS/rest/services/"
    "FS_VCGI_OPENDATA_Emergency_SiteStructureAddressPoint_point_SP_v1_VIEW/FeatureServer/0/query"
)
# Names as they appear in the Inc_Muni field. Essex split in 2022: "Essex Town" and "Essex Junction City".
TOWNS = ["Jericho", "Essex Town", "Essex Junction City", "Hinesburg", "Richmond", "Williston"]
PAGE = 2000
OUT = Path(__file__).resolve().parent.parent / "data" / "addresses.json"


def fetch_page(offset: int) -> list[dict]:
    towns = ",".join(f"'{t}'" for t in TOWNS)
    params = {
        "where": f"Inc_Muni IN ({towns}) AND Category = 'Residential'",
        "outFields": "ESITEID,PRIMARYADDRESS,Inc_Muni,SITETYPE,ResUnitCnt",
        "outSR": "4326",
        "orderByFields": "OBJECTID",
        "resultOffset": str(offset),
        "resultRecordCount": str(PAGE),
        "f": "json",
    }
    url = SERVICE + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=60) as r:
        data = json.load(r)
    if "error" in data:
        raise RuntimeError(data["error"])
    return data["features"]


def main() -> None:
    rows, offset = [], 0
    while True:
        feats = fetch_page(offset)
        for f in feats:
            a, g = f["attributes"], f.get("geometry")
            if not g:
                continue
            rows.append({
                "id": a["ESITEID"],
                "lat": round(g["y"], 6),
                "lon": round(g["x"], 6),
                "town": a["Inc_Muni"],
                "addr": a["PRIMARYADDRESS"],
                "type": a["SITETYPE"],
                "units": a["ResUnitCnt"] or 1,
            })
        print(f"  fetched {offset + len(feats)}", file=sys.stderr)
        if len(feats) < PAGE:
            break
        offset += PAGE
    OUT.write_text(json.dumps(rows, separators=(",", ":")))
    by_town: dict[str, int] = {}
    for r in rows:
        by_town[r["town"]] = by_town.get(r["town"], 0) + 1
    print(f"wrote {len(rows)} addresses to {OUT}")
    for t, n in sorted(by_town.items()):
        print(f"  {t:22} {n}")


if __name__ == "__main__":
    main()
