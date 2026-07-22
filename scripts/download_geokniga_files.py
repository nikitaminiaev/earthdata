#!/usr/bin/env python3
"""Phase 3: download all GeoKniga map files locally.

Reads geokniga_catalog_files.geojson, downloads each file to:
  /mnt/hdd2/Earthdata/10_server/data/geokniga_files/{map_id}/{file_index}_{sanitized_name}

Supports resume: skips files that already exist with matching size.
"""

import sys
import json
import time
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import unquote

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

VECS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"
INPUT = VECS_DIR / "geokniga_catalog_files.geojson"
FILES_DIR = Path(__file__).resolve().parent.parent / "data" / "geokniga_files"
MANIFEST_FILE = VECS_DIR / "geokniga_download_manifest.json"

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; GeoKnigaDownloader/1.0)'}
MAX_WORKERS = 4
REQUEST_DELAY = 0.5  # between requests per map


def sanitize_filename(name: str):
    name = name.strip().replace('/', '_').replace('\\', '_')
    name = ''.join(c for c in name if c.isprintable() and c not in '<>:"|?*')
    return name[:120]


def get_remote_size(url: str):
    try:
        r = requests.head(url, headers=HEADERS, timeout=10, allow_redirects=True)
        return int(r.headers.get('content-length', 0))
    except Exception:
        return 0


def download_file(map_id: int, file_info: dict, idx: int):
    """Download a single file. Returns (map_id, idx, success, bytes_downloaded)."""
    url = file_info['url']
    name = file_info['name']
    size_str = file_info.get('size_str', '')
    ext = file_info.get('ext', '')

    map_dir = FILES_DIR / str(map_id)
    map_dir.mkdir(parents=True, exist_ok=True)

    # Build filename: index_name.ext
    base = sanitize_filename(name)
    if not base:
        base = f"file_{idx}"
    if ext and not base.endswith('.' + ext):
        fname = f"{idx:02d}_{base}.{ext}"
    else:
        fname = f"{idx:02d}_{base}"
    fpath = map_dir / fname

    # Check if already downloaded
    if fpath.exists():
        local_size = fpath.stat().st_size
        expected_size = get_remote_size(url)
        if expected_size > 0 and local_size == expected_size:
            return (map_id, idx, True, local_size)
        if fpath.stat().st_size > 1024:  # at least 1KB — assume valid
            return (map_id, idx, True, local_size)

    # Download
    try:
        r = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        if r.status_code != 200:
            return (map_id, idx, False, 0)
        total = 0
        with open(fpath, 'wb') as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
        return (map_id, idx, True, total)
    except Exception as e:
        print(f"  Error [{map_id}/{idx}]: {e}", file=sys.stderr)
        if fpath.exists():
            fpath.unlink()
        return (map_id, idx, False, 0)


def main():
    if not INPUT.exists():
        print(f"Not found: {INPUT}"); sys.exit(1)

    with open(INPUT) as f:
        fc = json.load(f)

    # Build download queue
    queue = []
    for feat in fc['features']:
        props = feat['properties']
        mid = props['id']
        files = props.get('files', [])
        for idx, finfo in enumerate(files):
            queue.append((mid, finfo, idx))

    total_files = len(queue)
    total_maps = sum(1 for f in fc['features'] if f['properties'].get('files_count', 0) > 0)
    print(f"Download queue: {total_files} files from {total_maps} maps")

    # Load manifest (already-completed maps)
    already_done = set()
    if MANIFEST_FILE.exists():
        try:
            with open(MANIFEST_FILE) as f:
                already_done = set(json.load(f))
            print(f"  Skipping {len(already_done)} already-completed maps")
        except Exception:
            pass

    # Filter queue: skip maps fully done
    remaining = [(mid, finfo, idx) for (mid, finfo, idx) in queue if mid not in already_done]
    print(f"  Remaining to download: {len(remaining)} files")

    if not remaining:
        print("All files already downloaded!")
        return

    # Download
    total_bytes = 0
    completed_maps = set(already_done)
    completed_files = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        for mid, finfo, idx in remaining:
            time.sleep(REQUEST_DELAY / MAX_WORKERS)
            fut = pool.submit(download_file, mid, finfo, idx)
            futures[fut] = (mid, idx)

        for fut in as_completed(futures):
            mid, idx, success, bcount = fut.result()
            if success:
                total_bytes += bcount
                completed_files += 1
                completed_maps.add(mid)

            if completed_files % 20 == 0 or completed_files == len(remaining):
                pct = completed_files * 100 / len(remaining)
                mb = total_bytes / 1048576
                print(f"  [{completed_files}/{len(remaining)}] {pct:.0f}% | {mb:.0f} MB", file=sys.stderr)

            # Save manifest periodically
            if completed_files % 100 == 0:
                with open(MANIFEST_FILE, 'w') as f:
                    json.dump(list(completed_maps), f)

    with open(MANIFEST_FILE, 'w') as f:
        json.dump(list(completed_maps), f)

    print(f"\nDone: {completed_files} files downloaded", file=sys.stderr)
    print(f"Total: {total_bytes / 1073741824:.1f} GB", file=sys.stderr)


if __name__ == '__main__':
    main()
