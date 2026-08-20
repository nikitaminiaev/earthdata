import json
import math
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "vectors"
INPUT = DATA_DIR / "osm_mining_enriched.geojson"
OVERpass = DATA_DIR / "osm_quarries_overpass.geojson"
OUTPUT = DATA_DIR / "osm_mining_enriched.geojson"

with open(INPUT) as f:
    quarries = json.load(f)["features"]

with open(OVERpass) as f:
    overpass_list = json.load(f)["features"]

index = {}
for feat in overpass_list:
    lon, lat = feat["geometry"]["coordinates"]
    key = (round(lat, 6), round(lon, 6))
    index.setdefault(key, []).append(feat)

matched = 0
new_resource = 0

for q in quarries:
    lon, lat = q["geometry"]["coordinates"]
    key = (round(lat, 6), round(lon, 6))

    matches = index.get(key)
    if not matches:
        key5 = (round(lat, 5), round(lon, 5))
        matches = index.get(key5)

    if not matches:
        for of in overpass_list:
            olon, olat = of["geometry"]["coordinates"]
            d = math.hypot(lon - olon, lat - olat)
            if d < 0.001:
                matches = [of]
                break

    if not matches:
        continue

    matched += 1
    op = matches[0]["properties"]
    qp = q["properties"]

    skip_keys = {"@id", "osm_type", "osm_id", "type"}
    for k, v in op.items():
        if k in skip_keys:
            continue
        if not v:
            continue
        if k == "resource" and qp.get("resource"):
            continue
        if k == "resource" and not qp.get("resource") and v:
            qp["resource"] = v
            qp["enriched_by"] = "overpass"
            new_resource += 1
            continue
        enriched_key = f"osm:{k}"
        qp[enriched_key] = v

with open(OUTPUT, "w") as f:
    json.dump({"type": "FeatureCollection", "features": quarries}, f, ensure_ascii=False)

print(f"Matched {matched}/{len(quarries)} features")
print(f"New resource from Overpass: {new_resource}")

remaining = sum(1 for q in quarries if not q["properties"].get("resource"))
print(f"Still unknown: {remaining}")
