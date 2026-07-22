#!/usr/bin/env python3
"""
Parse GeoKniga map catalog → GeoJSON polygon layer.

1. Scrape /maps?page=N (all pages)
2. Convert Soviet nomenclature → bbox
3. Output GeoJSON with polygons colored by scale
"""

import sys
import json
import re
import math
import time
from pathlib import Path
from html.parser import HTMLParser

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

VECS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"
OUTPUT = VECS_DIR / "geokniga_catalog.geojson"
BASE_URL = "https://geokniga.org"
TOTAL_PAGES = 786
DELAY = 0.3  # polite delay between pages

# ── Roman numerals ──
ROMAN = {
    'I':1,'II':2,'III':3,'IV':4,'V':5,'VI':6,'VII':7,'VIII':8,'IX':9,'X':10,
    'XI':11,'XII':12,'XIII':13,'XIV':14,'XV':15,'XVI':16,'XVII':17,'XVIII':18,
    'XIX':19,'XX':20,'XXI':21,'XXII':22,'XXIII':23,'XXIV':24,'XXV':25,'XXVI':26,
    'XXVII':27,'XXVIII':28,'XXIX':29,'XXX':30,'XXXI':31,'XXXII':32,'XXXIII':33,
    'XXXIV':34,'XXXV':35,'XXXVI':36,
}

# 1:50K Cyrillic quadrant letters
CYR_QUAD = {'А':0,'Б':1,'В':2,'Г':3,'A':0,'B':1,'C':2,'D':3}
# 1:25K lowercase
CYR_25K = {'а':0,'б':1,'в':2,'г':3,'a':0,'b':1,'v':2,'g':3}

# ── Nomenclature → bbox ──
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

def _lat_of(letter: str):
    idx = LETTERS.find(letter.upper())
    if idx < 0 or idx > 21:
        return None
    return idx * 4

def _lon_of(zone: int):
    return (zone - 31) * 6

_nomer_re = re.compile(r'^([A-Za-z])-(\d{1,2})(?:-(\d+))?(?:-([А-ЯA-Za-z]))?(?:-([а-яa-z]))?$')
_roman_re = re.compile(r'^([A-Za-z])-(\d{1,2})-([IVXLCDM]+)$')

def _parse_single_nomer(nomer: str):
    """Parse a single nomenclature string. Returns (lat_min, lat_max, lon_min, lon_max) or None."""
    nomer = nomer.strip()
    if not nomer:
        return None

    m = _roman_re.match(nomer)
    if m:
        letter, zone_s, roman = m.groups()
        lat0 = _lat_of(letter)
        if lat0 is None: return None
        sn = ROMAN.get(roman)
        if sn is None or sn < 1 or sn > 36: return None
        zn = int(zone_s)
        lon0 = _lon_of(zn)
        r, c = (sn - 1) // 6, (sn - 1) % 6
        return (lat0 + r * 2/3, lat0 + (r + 1) * 2/3, lon0 + c * 1, lon0 + (c + 1) * 1)

    m = _nomer_re.match(nomer)
    if not m:
        return None
    letter, zone_s, part1, part2, part3 = m.groups()
    lat0 = _lat_of(letter)
    if lat0 is None: return None
    zn = int(zone_s)
    lon0 = _lon_of(zn)

    if not part1:
        if part2:
            idx = CYR_QUAD.get(part2.upper())
            if idx is not None:
                sr, sc = idx // 2, idx % 2
                lat_min = lat0 + sr * 2
                lat_max = lat_min + 2
                lon_min = lon0 + sc * 3
                lon_max = lon_min + 3
                return (lat_min, lat_max, lon_min, lon_max)
        return (lat0, lat0 + 4, lon0, lon0 + 6)

    if part1.isdigit():
        sn = int(part1)
        if 1 <= sn <= 144:
            r, c = (sn - 1) // 12, (sn - 1) % 12
            lat_min = lat0 + r * 20/60
            lat_max = lat_min + 20/60
            lon_min = lon0 + c * 30/60
            lon_max = lon_min + 30/60
            if part2:
                idx = CYR_QUAD.get(part2.upper())
                if idx is not None:
                    sr, sc = idx // 2, idx % 2
                    lat_min2 = lat_min + sr * 10/60
                    lat_max2 = lat_min2 + 10/60
                    lon_min2 = lon_min + sc * 15/60
                    lon_max2 = lon_min2 + 15/60
                    lat_min, lat_max = lat_min2, lat_max2
                    lon_min, lon_max = lon_min2, lon_max2
                    if part3:
                        idx3 = CYR_25K.get(part3.lower())
                        if idx3 is not None:
                            sr3, sc3 = idx3 // 2, idx3 % 2
                            lat_min = lat_min2 + sr3 * 5/60
                            lat_max = lat_min2 + (sr3 + 1) * 5/60
                            lon_min = lon_min2 + sc3 * 7.5/60
                            lon_max = lon_min2 + (sc3 + 1) * 7.5/60
            return (lat_min, lat_max, lon_min, lon_max)
        if 1 <= sn <= 36:
            r, c = (sn - 1) // 6, (sn - 1) % 6
            lat_min = lat0 + r * 40/60
            lat_max = lat_min + 40/60
            lon_min = lon0 + c * 1
            lon_max = lon_min + 1
            return (lat_min, lat_max, lon_min, lon_max)

    return None

def nomer_to_bboxes(nomer: str):
    """Parse potentially compound nomenclature → list of bboxes.

    Handles:
    - Single: K-35, K-35-073, K-35-VI
    - Compound: K-37-VI, XII (two Roman numerals)
    - Compound: K-(37),(38) (two zones)
    - Compound: K-37,38; L-37,38 (semicolons)
    - Compound: K-35-085-А (Доспат); K-35-099-А (Дрангово) (with notes)
    """
    if not nomer:
        return []

    # Strip parenthetical notes containing Cyrillic text (place names)
    cleaned = re.sub(r'\([^)]*[а-яА-Я][^)]*\)', '', nomer).strip()
    # Remove remaining empty parens
    cleaned = re.sub(r'\(\s*\)', '', cleaned).strip()
    # Normalize whitespace
    cleaned = re.sub(r'\s*;\s*', ';', cleaned)
    cleaned = re.sub(r'\s*,\s*', ',', cleaned)

    result = []

    # Split by semicolon (multiple distinct map sheets)
    parts = [p.strip() for p in cleaned.split(';') if p.strip()]
    for part in parts:
        part_result = _parse_nomer_part(part)
        result.extend(part_result)

    return result if result else result  # return [] if nothing found


def _parse_nomer_part(part: str):
    """Parse a single part of a compound nomenclature."""
    # First try as a single nomer
    bbox = _parse_single_nomer(part)
    if bbox:
        return [bbox]

    # Try: K-37-VI, XII  (same letter+zone, multiple Roman numerals)
    romans_pair = re.match(r'^([A-Za-z])-(\d{1,2})-([IVXLCDM]+),([IVXLCDM]+)$', part)
    if romans_pair:
        letter, zone_s, r1, r2 = romans_pair.groups()
        return _parse_romans_pair(letter, zone_s, [r1, r2])

    # Try: K-37-VI, VII, VIII (three or more Roman numerals)
    romans_multi = re.match(r'^([A-Za-z])-(\d{1,2})-([IVXLCDM]+(?:,[IVXLCDM]+)+)$', part)
    if romans_multi:
        letter, zone_s, romans_str = romans_multi.groups()
        romans = romans_str.split(',')
        return _parse_romans_pair(letter, zone_s, romans)

    # Try: K-(37),(38) — two zones in parentheses
    zones_parens = re.match(r'^([A-Za-z])-\((\d{1,2})\),\((\d{1,2})\)$', part)
    if zones_parens:
        letter, z1, z2 = zones_parens.groups()
        bboxes = []
        for z in (z1, z2):
            b = _parse_single_nomer(f"{letter}-{z}")
            if b:
                bboxes.append(b)
        return bboxes

    # Try: K-37,38 — two zone numbers
    zones_pair = re.match(r'^([A-Za-z])-(\d{1,2}),(\d{1,2})$', part)
    if zones_pair:
        letter, z1, z2 = zones_pair.groups()
        bboxes = []
        for z in (z1, z2):
            b = _parse_single_nomer(f"{letter}-{z}")
            if b:
                bboxes.append(b)
        return bboxes

    # Try: K-37-IX,XV — Roman numerals after a number
    romans_after_zone = re.match(r'^([A-Za-z])-(\d{1,2})-(\d+)-([IVXLCDM]+),([IVXLCDM]+)$', part)
    if romans_after_zone:
        letter, zone_s, num_s, r1, r2 = romans_after_zone.groups()
        bboxes = []
        for r in (r1, r2):
            b = _parse_single_nomer(f"{letter}-{zone_s}-{num_s}-{r}")
            if b:
                bboxes.append(b)
        return bboxes

    return []


def _parse_romans_pair(letter, zone_s, romans):
    """Parse multiple Roman numerals with same letter and zone."""
    bboxes = []
    for r in romans:
        b = _parse_single_nomer(f"{letter}-{zone_s}-{r}")
        if b:
            bboxes.append(b)
    return bboxes


def nomer_to_bbox(nomer: str):
    """Legacy: return first bbox or None."""
    bboxes = nomer_to_bboxes(nomer)
    return bboxes[0] if bboxes else None

    # 1:200K Roman numeral
    m = _roman_re.match(nomer)
    if m:
        letter, zone_s, roman = m.groups()
        lat0 = _lat_of(letter)
        if lat0 is None: return None
        sn = ROMAN.get(roman)
        if sn is None or sn < 1 or sn > 36: return None
        zn = int(zone_s)
        lon0 = _lon_of(zn)
        r, c = (sn - 1) // 6, (sn - 1) % 6
        return (lat0 + r * 2/3, lat0 + (r + 1) * 2/3, lon0 + c * 1, lon0 + (c + 1) * 1)

    m = _nomer_re.match(nomer)
    if not m:
        return None
    letter, zone_s, part1, part2, part3 = m.groups()
    lat0 = _lat_of(letter)
    if lat0 is None: return None
    zn = int(zone_s)
    lon0 = _lon_of(zn)

    if not part1:
        if part2:
            idx = CYR_QUAD.get(part2.upper())
            if idx is not None:
                # 1:500K quadrant of 1:1M
                sr, sc = idx // 2, idx % 2
                lat_min = lat0 + sr * 2
                lat_max = lat_min + 2
                lon_min = lon0 + sc * 3
                lon_max = lon_min + 3
                return (lat_min, lat_max, lon_min, lon_max)
        return (lat0, lat0 + 4, lon0, lon0 + 6)

    # part1: 1:100K (001-144) or 1:200K Arabic (001-036)
    if part1.isdigit():
        sn = int(part1)
        if 1 <= sn <= 144:
            r, c = (sn - 1) // 12, (sn - 1) % 12
            lat_min = lat0 + r * 20/60
            lat_max = lat_min + 20/60
            lon_min = lon0 + c * 30/60
            lon_max = lon_min + 30/60

            if part2:
                idx = CYR_QUAD.get(part2.upper())
                if idx is not None:
                    sr, sc = idx // 2, idx % 2
                    lat_min2 = lat_min + sr * 10/60
                    lat_max2 = lat_min2 + 10/60
                    lon_min2 = lon_min + sc * 15/60
                    lon_max2 = lon_min2 + 15/60
                    lat_min, lat_max = lat_min2, lat_max2
                    lon_min, lon_max = lon_min2, lon_max2

                    if part3:
                        idx3 = CYR_25K.get(part3.lower())
                        if idx3 is not None:
                            sr3, sc3 = idx3 // 2, idx3 % 2
                            lat_min = lat_min2 + sr3 * 5/60
                            lat_max = lat_min2 + (sr3 + 1) * 5/60
                            lon_min = lon_min2 + sc3 * 7.5/60
                            lon_max = lon_min2 + (sc3 + 1) * 7.5/60
            return (lat_min, lat_max, lon_min, lon_max)

        if 1 <= sn <= 36:
            r, c = (sn - 1) // 6, (sn - 1) % 6
            lat_min = lat0 + r * 40/60
            lat_max = lat_min + 40/60
            lon_min = lon0 + c * 1
            lon_max = lon_min + 1
            return (lat_min, lat_max, lon_min, lon_max)

    return None

# ── Scale helpers ──
def _scale_color(scale: str):
    if not scale:
        return "#999999", "unknown"
    s = scale.strip().replace(" ", "")
    if s.startswith("1:"):
        denom = int(s[2:])
        if denom <= 25000:
            return "#e74c3c", "1:25K"
        elif denom <= 50000:
            return "#e67e22", "1:50K"
        elif denom <= 100000:
            return "#f1c40f", "1:100K"
        elif denom <= 200000:
            return "#2ecc71", "1:200K"
        elif denom <= 500000:
            return "#3498db", "1:500K"
        elif denom <= 1000000:
            return "#9b59b6", "1:1M"
        else:
            return "#95a5a6", "small"
    return "#999999", s[:20]

# ── Scraper ──
def _extract_nomer_from_block(block):
    """Extract nomenclature from a map node block."""
    m = re.search(r'<div class="maps_body_nomnum">.*?<a[^>]*>([^<]+)</a>', block, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""

def _extract_scale_from_block(block):
    """Extract scale from a map node block."""
    m = re.search(r'Масштаб:</legend>.*?<a[^>]*>([^<]+)</a>', block, re.DOTALL)
    if m:
        return m.group(1).strip()
    return ""

def _extract_year_from_block(block):
    """Extract year from a map node block."""
    m = re.search(r'Карта составлена:</legend>.*?(\d{4})\s*г', block, re.DOTALL)
    if m:
        return m.group(1)
    return ""

def _get_node_blocks(html):
    """Split HTML into blocks for each map node."""
    blocks = []
    for m in re.finditer(r'<div id="node-(\d+)"', html):
        nid = m.group(1)
        start = m.start()
        # Find matching closing div by tracking depth of <div>
        depth = 1
        i = m.end()
        while i < len(html) and depth > 0:
            open_tag = html.find('<div', i)
            close_tag = html.find('</div>', i)
            if open_tag == -1 and close_tag == -1:
                break
            if close_tag == -1 or (open_tag != -1 and open_tag < close_tag):
                depth += 1
                i = open_tag + 4
            else:
                depth -= 1
                i = close_tag + 6
        block = html[start:i]
        blocks.append((int(nid), block))
    return blocks

def scrape_page(page_num):
    url = f"{BASE_URL}/maps?page={page_num}"
    headers = {'User-Agent': 'Mozilla/5.0 (compatible; GeoKnigaScraper/1.0)'}
    try:
        r = requests.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return []
        html = r.text
    except Exception as e:
        print(f"  Error page {page_num}: {e}", file=sys.stderr)
        return []

    results = []
    for nid, block in _get_node_blocks(html):
        # Title
        title_m = re.search(r'<H2><a[^>]*>(.*?)</a></H2>', block, re.DOTALL)
        title = title_m.group(1).strip() if title_m else ""

        results.append({
            'id': nid,
            'title': title,
            'scale': _extract_scale_from_block(block),
            'year': _extract_year_from_block(block),
            'nomer': _extract_nomer_from_block(block),
        })

    return results


def main():
    all_maps = []
    start_page = 0
    end_page = TOTAL_PAGES - 1  # 785 (0-indexed)

    # Check if we already have a GeoJSON to resume
    existing_ids = set()
    if OUTPUT.exists():
        try:
            with open(OUTPUT) as f:
                fc = json.load(f)
            for feat in fc['features']:
                existing_ids.add(feat['properties']['id'])
            print(f"Resuming: {len(existing_ids)} existing maps", file=sys.stderr)
        except:
            pass

    for page in range(start_page, end_page + 1):
        print(f"Page {page+1}/{end_page+1}...", file=sys.stderr)
        maps = scrape_page(page)
        print(f"  Found {len(maps)} maps", file=sys.stderr)
        if not maps:
            break
        for m in maps:
            if m['id'] in existing_ids:
                continue
            all_maps.append(m)
            existing_ids.add(m['id'])
        time.sleep(DELAY)

    print(f"Total new maps: {len(all_maps)}", file=sys.stderr)
    
    # Build features
    features = []
    no_bbox = 0
    for m in all_maps:
        bboxes = nomer_to_bboxes(m['nomer'])
        color, scale_label = _scale_color(m['scale'])
        if bboxes:
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
        else:
            no_bbox += 1
            geom = None
        
        feat = {
            "type": "Feature",
            "properties": {
                "id": m['id'],
                "title": m['title'],
                "scale": m['scale'],
                "year": m['year'],
                "nomer": m['nomer'],
                "url": f"{BASE_URL}/maps/{m['id']}",
                "fill": color,
                "stroke": "#333",
            }
        }
        if geom:
            feat["geometry"] = geom
        else:
            feat["geometry"] = None
        features.append(feat)

    fc = {
        "type": "FeatureCollection",
        "features": features,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, 'w') as f:
        json.dump(fc, f, ensure_ascii=False)
    
    print(f"Written {len(features)} features to {OUTPUT}", file=sys.stderr)
    print(f"  With bbox: {len(features) - no_bbox}", file=sys.stderr)
    print(f"  Without bbox: {no_bbox}", file=sys.stderr)


if __name__ == '__main__':
    main()
