import json
import time
import requests
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "vectors"
OUTPUT = DATA_DIR / "osm_quarries_overpass.geojson"

OVERPASS = "https://overpass-api.de/api/interpreter"

bboxes = [
    ("europe-west", 43, 19, 70, 45),
    ("europe-north", 60, 28, 72, 60),
    ("europe-east", 43, 45, 60, 62),
    ("caucasus", 41, 36, 47, 50),
    ("ural", 50, 50, 70, 70),
    ("west-siberia", 50, 65, 73, 90),
    ("east-siberia-west", 50, 85, 73, 115),
    ("east-siberia-east", 55, 110, 73, 145),
    ("far-east-north", 60, 145, 73, 190),
    ("far-east-south", 42, 120, 60, 145),
]

all_elements = []
failed = []

for name, s, w, n, e in bboxes:
    query = (
        f'[out:json][timeout:180];'
        f'(node["landuse"="quarry"]({s},{w},{n},{e});'
        f'way["landuse"="quarry"]({s},{w},{n},{e});'
        f'relation["landuse"="quarry"]({s},{w},{n},{e}););'
        f'out center tags qt;'
    )

    for attempt in range(4):
        delay = 5 * (2 ** attempt)
        if attempt > 0:
            print(f"  Retry {attempt+1} in {delay}s...")
            time.sleep(delay)

        try:
            resp = requests.post(
                OVERPASS,
                data={"data": query},
                timeout=200,
                headers={"User-Agent": "Earthdata/1.0"},
            )
            if resp.status_code == 429:
                print(f"  Rate limited, waiting...")
                time.sleep(30)
                continue
            if resp.status_code == 504:
                print(f"  Timeout, will retry...")
                continue
            if resp.status_code == 400:
                body = resp.text[:200]
                print(f"  Bad request: {body}")
                failed.append(name)
                break
            if resp.status_code != 200:
                print(f"  HTTP {resp.status_code}: {resp.text[:200]}")
                continue

            data = resp.json()
            elements = data.get("elements", [])
            print(f"{name}: {len(elements)} elements")
            all_elements.extend(elements)
            break
        except Exception as e:
            print(f"  Error: {e}")
            continue
    else:
        print(f"{name}: FAILED after 4 attempts")
        failed.append(name)

    time.sleep(3)

print(f"\nTotal: {len(all_elements)} elements from {len(bboxes)-len(failed)}/{len(bboxes)} regions")
if failed:
    print(f"Failed: {failed}")

features = []
tag_keys = set()

for el in all_elements:
    osm_type = el["type"]
    osm_id = el["id"]
    tags = el.get("tags", {})

    if not tags:
        continue

    if "center" in el:
        lon, lat = el["center"]["lon"], el["center"]["lat"]
    else:
        lon, lat = el["lon"], el["lat"]

    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"@id": f"{osm_type}/{osm_id}", "osm_type": osm_type, "osm_id": osm_id, **tags},
    }
    features.append(feature)
    tag_keys.update(tags.keys())

geojson = {"type": "FeatureCollection", "features": features}
with open(OUTPUT, "w") as f:
    json.dump(geojson, f, ensure_ascii=False)

print(f"\nSaved: {len(features)} features with tags")
print(f"All tag keys ({len(tag_keys)}): {sorted(tag_keys)}")
print(f"Output: {OUTPUT}")
