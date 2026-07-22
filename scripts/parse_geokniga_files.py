#!/usr/bin/env python3
"""Phase 2: scrape individual map pages → extract files + metadata.

Reads geokniga_catalog.geojson, fetches /maps/{id} for each, extracts:
- Files (name, url, size) from div.maps_body_files
- Extra metadata: editor, publisher, year, series, map_function, language, labels

Output: geokniga_catalog_files.geojson (with 'files' array in properties)
Supports resume: skips maps that already have 'files' in properties.
"""

import sys
import json
import re
import time
from pathlib import Path
from html.parser import HTMLParser

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

VECS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"
INPUT = VECS_DIR / "geokniga_catalog.geojson"
OUTPUT = VECS_DIR / "geokniga_catalog_files.geojson"
BASE_URL = "https://geokniga.org"
DELAY = 1.0
CHECKPOINT_EVERY = 50
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; GeoKnigaScraper/2.0)'}
RETRIES = 3

session = requests.Session()
session.headers.update(HEADERS)
session.keep_alive = False


def resolve_url(href: str):
    if href.startswith('http'):
        return href
    if href.startswith('//'):
        return 'https:' + href
    if href.startswith('/'):
        return BASE_URL + href
    cleaned = href.replace('../../', '/sites/geokniga/')
    if cleaned.startswith('/'):
        return BASE_URL + cleaned
    return BASE_URL + '/' + cleaned


def parse_page(html: str):
    result = {'files': [], 'files_total_size': '', 'files_count': 0}

    # Extract files from div.maps_body_files
    files_section = re.search(
        r'<div class="maps_body_files">\s*<fieldset[^>]*>.*?<legend>Скачать</legend>(.*?)</fieldset>',
        html, re.DOTALL
    )
    if not files_section:
        return result
    files_html = files_section.group(1)

    for m in re.finditer(
        r'<div class="filefield-file">.*?<a\s+href="([^"]*)"[^>]*>([^<]+)</a>\s*\(([^)]+)\)',
        files_html, re.DOTALL
    ):
        href, label, size = m.group(1), m.group(2).strip(), m.group(3).strip()
        url = resolve_url(href)
        ext = url.rsplit('.', 1)[-1].lower() if '.' in url else ''
        result['files'].append({
            'name': label,
            'url': url,
            'size_str': size,
            'ext': ext,
        })

    result['files_count'] = len(result['files'])
    if result['files']:
        total_bytes = 0
        for f in result['files']:
            sz = f['size_str'].lower()
            try:
                if sz.endswith('k'):
                    total_bytes += int(float(sz[:-1]) * 1024)
                elif sz.endswith('m'):
                    total_bytes += int(float(sz[:-1]) * 1024 * 1024)
                elif sz.endswith('g'):
                    total_bytes += int(float(sz[:-1]) * 1024 * 1024 * 1024)
                elif sz.endswith('b'):
                    total_bytes += int(float(sz[:-1]))
            except ValueError:
                pass
        if total_bytes >= 1073741824:
            result['files_total_size'] = f"{total_bytes / 1073741824:.1f} GB"
        elif total_bytes >= 1048576:
            result['files_total_size'] = f"{total_bytes / 1048576:.1f} MB"
        elif total_bytes >= 1024:
            result['files_total_size'] = f"{total_bytes / 1024:.1f} KB"
        else:
            result['files_total_size'] = f"{total_bytes} B"

    # Extract extra metadata
    # Editor
    em = re.search(r'<legend>Редактор\(ы\):</legend>.*?<cpan[^>]*>([^<]+)</cpan>', html, re.DOTALL)
    if em:
        result['editor'] = em.group(1).strip()

    # Publisher + year
    cm = re.search(r'<legend>Карта составлена:</legend>.*?<cpan[^>]*>([^<]+)</cpan>\s*,\s*(\d{4})\s*г', html, re.DOTALL)
    if cm:
        result['publisher'] = cm.group(1).strip()
        result['pub_year'] = cm.group(2)

    # Series
    sm = re.search(r'<legend>Серии карт:</legend>.*?<a\s+href=[^>]*>([^<]+)</a>', html, re.DOTALL)
    if sm:
        result['series'] = sm.group(1).strip()

    # Map function
    fm = re.search(r'<legend>Назначение карты:</legend>([^<]+)</fieldset>', html, re.DOTALL)
    if fm:
        result['map_function'] = fm.group(1).strip()

    # Language
    lm = re.search(r'<legend>Язык\(и\)</legend>([^<]+)</fieldset>', html, re.DOTALL)
    if lm:
        result['language'] = lm.group(1).strip()

    # Labels
    lbm = re.search(r'<div class="maps_body_label">.*?<fieldset[^>]*>.*?<legend>Метки</legend>(.*?)</fieldset>', html, re.DOTALL)
    if lbm:
        labels_html = lbm.group(1)
        labels = re.findall(r'<a[^>]*>([^<]+)</a>', labels_html)
        if labels:
            result['labels'] = labels

    return result


def main():
    if not INPUT.exists():
        print(f"Not found: {INPUT}"); sys.exit(1)

    with open(INPUT) as f:
        fc = json.load(f)

    checkpoint_data = {}
    if OUTPUT.exists():
        try:
            with open(OUTPUT) as f:
                existing = json.load(f)
            for feat in existing['features']:
                if feat['properties'].get('files'):
                    checkpoint_data[feat['properties']['id']] = feat['properties']
            print(f"Resume: {len(checkpoint_data)} maps already have file data")
        except Exception:
            pass

    features = fc['features']
    total = len(features)
    processed = 0

    for i, feat in enumerate(features):
        mid = feat['properties']['id']
        if mid in checkpoint_data:
            feat['properties'].update(checkpoint_data[mid])
            processed += 1
            continue

        url = f"{BASE_URL}/maps/{mid}"
        data = None
        for attempt in range(RETRIES):
            try:
                r = session.get(url, timeout=30)
                if r.status_code == 200:
                    data = parse_page(r.text)
                    break
                if r.status_code == 404:
                    print(f"  [{i+1}/{total}] {mid}: 404 — skipped", file=sys.stderr)
                    data = parse_page('<div class="maps_body_files"><fieldset><legend>Скачать</legend></fieldset></div>')
                    break
                print(f"  [{i+1}/{total}] {mid}: HTTP {r.status_code} (attempt {attempt+1})", file=sys.stderr)
            except Exception as e:
                print(f"  [{i+1}/{total}] {mid}: {e} (attempt {attempt+1})", file=sys.stderr)
            if attempt < RETRIES - 1:
                time.sleep(5 * (2 ** attempt))
        if data is None:
            continue

        feat['properties'].update(data)
        processed += 1
        time.sleep(DELAY)

        if (i + 1) % CHECKPOINT_EVERY == 0:
            with open(OUTPUT, 'w') as f:
                json.dump(fc, f, ensure_ascii=False)
            print(f"  Checkpoint [{i+1}/{total}], processed={processed}", file=sys.stderr)

    with open(OUTPUT, 'w') as f:
        json.dump(fc, f, ensure_ascii=False)

    with_files = sum(1 for f in fc['features'] if f['properties'].get('files_count', 0) > 0)
    total_size_files = [f['properties'].get('files_total_size', '') for f in fc['features'] if f['properties'].get('files_total_size')]
    print(f"\nDone: {processed} maps processed", file=sys.stderr)
    print(f"With files: {with_files}", file=sys.stderr)
    print(f"Written to {OUTPUT}", file=sys.stderr)


if __name__ == '__main__':
    main()
