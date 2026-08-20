import json
import csv
import math
from pathlib import Path
from collections import Counter

DATA_DIR = Path(__file__).parent.parent / "data" / "vectors"
MRDS_FILE = Path(__file__).parent.parent.parent / "04_mrds" / "mrds.csv"
INPUT = DATA_DIR / "osm_mining_enriched.geojson"
OUTPUT = DATA_DIR / "osm_mining_enriched.geojson"

commodity_map = {
    "Gold": "gold",
    "Gold, Silver": "gold",
    "Gold, Platinum": "gold",
    "Platinum, Gold": "gold",
    "Silver, Gold": "gold",
    "Iron": "iron_ore",
    "Iron, Manganese": "iron_ore",
    "Copper": "copper",
    "Copper, Zinc": "copper",
    "Copper, Nickel": "copper",
    "Copper, Lead, Zinc": "copper",
    "Manganese": "manganese",
    "Mercury": "mercury",
    "Chromium": "chromium",
    "Tin": "tin",
    "Tungsten": "tungsten",
    "Molybdenum": "molybdenum",
    "Nickel": "nickel",
    "Nickel, Copper": "nickel",
    "Cobalt": "cobalt",
    "Uranium": "uranium",
    "Lead, Zinc": "lead",
    "Zinc, Lead": "zinc",
    "Aluminum": "bauxite",
    "Titanium": "titanium",
    "Platinum": "platinum",
    "Palladium, Platinum": "platinum",
    "Antimony": "antimony",
    "Graphite": "graphite",
    "Magnesite": "magnesite",
    "Fluorine-Fluorite": "fluorite",
    "Phosphorus-Phosphates": "phosphate",
    "Asbestos": "asbestos",
    "Barium-Barite": "barite",
    "Beryllium": "beryllium",
    "Bismuth": "bismuth",
    "Boron-Borates": "boron",
    "Cesium": "cesium",
    "Diamond": "diamond",
    "Gallium": "gallium",
    "Germanium": "germanium",
    "Hafnium": "hafnium",
    "Indium": "indium",
    "Lithium": "lithium",
    "Magnesium": "magnesium",
    "Niobium (Columbium)": "niobium",
    "Rare Earths": "rare_earth",
    "Rhenium": "rhenium",
    "Scandium": "scandium",
    "Selenium": "selenium",
    "Silver": "silver",
    "Strontium": "strontium",
    "Tantalum": "tantalum",
    "Tellurium": "tellurium",
    "Thorium": "thorium",
    "Vanadium": "vanadium",
    "Zirconium": "zirconium",
    "Sand and Gravel": "sand",
    "Stone, Crushed": "aggregate",
    "Stone, Dimension": "dimension_stone",
    "Clay": "clay",
    "Limestone": "limestone",
    "Phosphate": "phosphate",
    "Sodium, Salt": "salt",
    "Sulfur": "sulfur",
    "Vermiculite": "vermiculite",
    "Wollastonite": "wollastonite",
    "Zeolites": "zeolite",
    "Coal": "coal",
    "Peat": "peat",
}

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

print("Loading MRDS Russian deposits...")
mrds = []
with open(MRDS_FILE) as f:
    for row in csv.DictReader(f):
        if row.get("country") != "Russia":
            continue
        lat = row.get("latitude", "").strip()
        lon = row.get("longitude", "").strip()
        if not lat or not lon:
            continue
        try:
            lat, lon = float(lat), float(lon)
        except ValueError:
            continue
        comm = (row.get("commod1") or "").strip()
        if not comm:
            continue
        mapped = commodity_map.get(comm)
        if mapped:
            mrds.append((lat, lon, mapped, comm))

print(f"Loaded {len(mrds)} deposits with mapped commodities")

with open(INPUT) as f:
    data = json.load(f)
quarries = data["features"]

unknown = [q for q in quarries if not q["properties"].get("resource")]
total = len(unknown)
print(f"Unknown quarries: {total}")

THRESHOLDS = [500, 1000, 2000, 5000, 10000]
matched_at = {t: 0 for t in THRESHOLDS}
matched_total = 0
commodity_counts = Counter()

for i, q in enumerate(unknown):
    if i % 1000 == 0:
        print(f"  Processing {i}/{total}...")
    qlat, qlon = q["geometry"]["coordinates"][1], q["geometry"]["coordinates"][0]
    best_dist = float("inf")
    best_comm = None

    for mlat, mlon, mapped, orig_comm in mrds:
        d = haversine(qlat, qlon, mlat, mlon)
        if d < best_dist:
            best_dist = d
            best_comm = mapped

    if best_comm and best_dist <= max(THRESHOLDS):
        for t in THRESHOLDS:
            if best_dist <= t:
                matched_at[t] += 1

        if best_dist <= 2000:
            q["properties"]["resource"] = best_comm
            q["properties"]["enriched_by"] = "mrds"
            q["properties"]["mrds_distance"] = round(best_dist)
            matched_total += 1
            commodity_counts[best_comm] += 1

with open(OUTPUT, "w") as f:
    json.dump(data, f, ensure_ascii=False)

print(f"\nMatched at thresholds:")
for t in THRESHOLDS:
    print(f"  < {t}m: {matched_at[t]}")

print(f"\nAssigned resource from MRDS: {matched_total}")
print(f"\nCommodity breakdown:")
for c, n in commodity_counts.most_common():
    print(f"  {c}: {n}")

remaining = sum(1 for q in quarries if not q["properties"].get("resource"))
print(f"\nStill unknown: {remaining} ({100*remaining/len(quarries):.1f}%)")
