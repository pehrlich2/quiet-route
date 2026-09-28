# Quiet Route planner

Plans every-other-week trash routes for an electric pickup + dump trailer, using real
Vermont address points and the OpenStreetMap road network, with dump runs to the
Williston transfer station.

## Setup

```sh
uv sync
uv run python scripts/fetch_addresses.py   # E911 residential address points -> data/addresses.json
uv run python scripts/build_graph.py       # OSM drive network -> data/roads.npz
uv run python -m planner.server            # http://localhost:8765
```

`uv run python -m planner.solve '{"customers": 800}'` solves from the command line and
writes `out/plan.json`.

## How it works

1. **Customers** are sampled from residential E911 address points in the chosen towns,
   either scattered or clustered (word of mouth).
2. **Service days**: 10 per two-week cycle. Either whole towns get days in proportion to
   their customers, or everything is cut into pie slices around the Williston dump.
3. **Routing**: each day is a capacitated vehicle routing problem solved with OR-Tools.
   Each trailer load is modelled as a "vehicle" that ends at the dump, so the solver
   decides which stops go in which load. Distances are shortest road paths.
4. **Recycling modes**: trash only; alternate weeks (trash in week A, recycling in
   week B, same weekday); or both carts in one visit with a split trailer. The split
   mode adds a second capacity dimension, so each compartment fills independently.
5. **Energy** is computed in the browser from each day's road miles, so battery,
   towing and winter settings update instantly without re-solving.

## Limits

- Addresses snap to the nearest road intersection or bend, so long driveways and
  dead-end roads are approximated.
- The solver treats roads as nodes to visit, not streets to sweep. Real trash routes
  also care about which side of the road the cans are on and avoiding left turns.
- "Max stops per day" scales each day's actual stop spacing; it is an estimate.
