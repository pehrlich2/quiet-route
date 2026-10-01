"""Build a static copy of the site in dist/ for GitHub Pages.

The live site runs on planner/server.py, which serves JSON under /api/ and can re-solve
routes. Pages can't run Python, so this copies the pages with relative paths, saves the
current plan and town counts as JSON files, and turns re-planning off. Private pages
(the owner's tax scenarios) are left out.
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB, DIST = ROOT / "web", ROOT / "dist"
sys.path.insert(0, str(ROOT))
PRIVATE = {"petertaxes.html"}


def main() -> None:
    from planner.solve import DEFAULTS, load_addresses, solve

    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(WEB, DIST, ignore=shutil.ignore_patterns(*PRIVATE))
    (DIST / "data").mkdir()

    # Data the server normally provides.
    plan_file = ROOT / "out" / "plan.json"
    plan = json.loads(plan_file.read_text()) if plan_file.exists() else solve()
    (DIST / "data" / "plan.json").write_text(json.dumps(plan, separators=(",", ":")))
    counts: dict[str, int] = {}
    for a in load_addresses():
        counts[a["town"]] = counts.get(a["town"], 0) + 1
    (DIST / "data" / "towns.json").write_text(json.dumps({"towns": counts, "defaults": DEFAULTS}))
    for name in ("addresses.json", "chargers.json"):
        shutil.copy(ROOT / "data" / name, DIST / "data" / name)

    # Absolute server paths -> relative static paths.
    rewrites = [
        ("/api/plan", "data/plan.json"),
        ("/api/towns", "data/towns.json"),
        ("/data/", "data/"),
        ("/web/", ""),
    ]
    for f in list(DIST.glob("*.html")) + list(DIST.glob("*.js")):
        text = f.read_text()
        for old, new in rewrites:
            text = text.replace(old, new)
        f.write_text(text)

    # Re-planning needs the Python server; say so instead of failing.
    routing = DIST / "routing.html"
    text = routing.read_text().replace(
        '<button id="solveBtn" type="submit">Re-plan routes</button>',
        '<button id="solveBtn" type="submit" disabled title="Needs the local planner server">Re-plan routes</button>'
        '<p class="note">This published copy shows a saved plan. Re-planning runs on the local planner '
        '(<code>uv run python -m planner.server</code>).</p>',
    )
    routing.write_text(text)

    (DIST / ".nojekyll").write_text("")
    (DIST / "index.html").exists() or print("warning: no index.html")
    print(f"built {DIST} ({sum(1 for _ in DIST.rglob('*') if _.is_file())} files), private pages left out: {', '.join(PRIVATE)}")


if __name__ == "__main__":
    main()
