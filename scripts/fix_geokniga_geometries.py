#!/usr/bin/env python3
"""Re-process existing GeoJSON — fix compound nomenclatures → polygons/MultiPolygons."""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_geokniga import nomer_to_bboxes, _scale_color, BASE_URL

VECS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"
INPUT = VECS_DIR / "geokniga_catalog.geojson"

def main():
    if not INPUT.exists():
        print(f"Not found: {INPUT}"); sys.exit(1)

    with open(INPUT) as f:
        fc = json.load(f)

    fixed = 0
    remained_empty = 0
    for feat in fc['features']:
        if feat['geometry']:
            continue

        nomer = feat['properties'].get('nomer', '')
        bboxes = nomer_to_bboxes(nomer)
        if not bboxes:
            remained_empty += 1
            continue

        polys = []
        for bbox in bboxes:
            lat_min, lat_max, lon_min, lon_max = bbox
            poly = [[lon_min, lat_min], [lon_max, lat_min],
                    [lon_max, lat_max], [lon_min, lat_max], [lon_min, lat_min]]
            polys.append(poly)

        if len(polys) == 1:
            geom = {"type": "Polygon", "coordinates": polys}
        else:
            geom = {"type": "MultiPolygon", "coordinates": [[p] for p in polys]}
        feat['geometry'] = geom

        color, _ = _scale_color(feat['properties'].get('scale', ''))
        feat['properties']['fill'] = color
        fixed += 1

    with open(INPUT, 'w') as f:
        json.dump(fc, f, ensure_ascii=False)

    print(f"Fixed: {fixed}, still empty: {remained_empty}")
    print(f"Total features: {len(fc['features'])}")

if __name__ == '__main__':
    main()
