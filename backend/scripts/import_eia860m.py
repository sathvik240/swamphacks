"""Regenerate data/sites.json from an EIA-860M workbook.

Download the latest "Preliminary Monthly Electric Generator Inventory" from
https://www.eia.gov/electricity/data/eia860m/ and run:

  pip install openpyxl
  python scripts/import_eia860m.py path/to/<month>_generator<year>.xlsx \
      --states FL GA AL LA MS MO TX --min-mw 500 --limit 80

Existing river/coastal annotations in sites.json are kept for plants that are
still present (matched by name + state).
"""
import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SITES = ROOT / "data" / "sites.json"

TZ_BY_STATE = {
    **dict.fromkeys("CT DE DC FL GA IN KY ME MD MA MI NH NJ NY NC OH PA RI SC VT VA WV".split(), "America/New_York"),
    **dict.fromkeys("AL AR IL IA KS LA MN MS MO NE ND OK SD TN TX WI".split(), "America/Chicago"),
    **dict.fromkeys("CO ID MT NM UT WY".split(), "America/Denver"),
    "AZ": "America/Phoenix", **dict.fromkeys("CA NV OR WA".split(), "America/Los_Angeles"),
}
# Panhandle FL and East TN are exceptions; fix by county if needed.


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--states", nargs="*")
    ap.add_argument("--min-mw", type=float, default=300)
    ap.add_argument("--limit", type=int, default=100)
    args = ap.parse_args()

    df = pd.read_excel(args.workbook, sheet_name="Operating", header=2)
    df = df.rename(columns=lambda c: str(c).strip())
    plants = (df.groupby(["Plant ID", "Plant Name", "Entity Name", "Plant State", "County", "Latitude", "Longitude"], dropna=False)
                .agg(mw=("Nameplate Capacity (MW)", "sum"), tech=("Technology", lambda s: "/".join(sorted(set(map(str, s)))[:2])))
                .reset_index())
    plants = plants[plants["mw"] >= args.min_mw].dropna(subset=["Latitude", "Longitude"])
    if args.states:
        plants = plants[plants["Plant State"].isin(args.states)]
    plants = plants.sort_values("mw", ascending=False).head(args.limit)

    old = {(s["name"].lower(), s["state"]): s for s in json.loads(SITES.read_text())["sites"]} if SITES.exists() else {}
    sites = []
    for _, p in plants.iterrows():
        st = p["Plant State"]
        prev = old.get((str(p["Plant Name"]).lower(), st), {})
        sites.append({
            "id": re.sub(r"[^a-z0-9]+", "-", f"{p['Plant Name']}-{st}".lower()).strip("-"),
            "name": str(p["Plant Name"]), "utility": str(p["Entity Name"]), "state": st, "county": str(p["County"]),
            "lat": round(float(p["Latitude"]), 4), "lon": round(float(p["Longitude"]), 4), "plant_type": p["tech"],
            "timezone": prev.get("timezone") or TZ_BY_STATE.get(st, "America/New_York"),
            "river": prev.get("river"), "coastal": prev.get("coastal", False),
        })
    if len(sites) < 50:
        print(f"warning: only {len(sites)} plants matched; the brief asks for at least 50", file=sys.stderr)
    SITES.write_text(json.dumps({"source": f"EIA-860M {Path(args.workbook).name}", "sites": sites}, indent=1))
    print(f"wrote {len(sites)} sites to {SITES}")


if __name__ == "__main__":
    main()
