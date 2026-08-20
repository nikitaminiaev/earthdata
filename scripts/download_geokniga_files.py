#!/usr/bin/env python3
"""Phase 3: download all GeoKniga map files locally.

Reads geokniga_catalog_files.geojson, downloads each file to:
  /mnt/hdd2/Earthdata/10_server/data/geokniga_files/{map_id}/{file_index}_{sanitized_name}

Supports resume: skips files that already exist with matching size.
"""

import sys
import json
import time
from pathlib import Path
import concurrent.futures

try:
    import requests
except ImportError:
    print("pip install requests"); sys.exit(1)

VECS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"
INPUT = VECS_DIR / "geokniga_catalog_files.geojson"
FILES_DIR = Path(__file__).resolve().parent.parent / "data" / "geokniga_files"
MANIFEST_FILE = VECS_DIR / "geokniga_download_manifest.json"

HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; GeoKnigaDownloader/1.0)'}
MAX_WORKERS = 2
REQUEST_DELAY = 1.0
RETRIES = 3
RETRY_BACKOFF = [5, 15, 45]

session = requests.Session()
session.headers.update(HEADERS)
session.keep_alive = False


def sanitize_filename(name: str):
    name = name.strip().replace('/', '_').replace('\\', '_')
    name = ''.join(c for c in name if c.isprintable() and c not in '<>:"|?*')
    return name[:120]


def download_file(map_id: int, file_info: dict, idx: int):
    """Download a single file with retries. Returns (map_id, idx, success, bytes_downloaded)."""
    url = file_info['url']
    name = file_info['name']
    ext = file_info.get('ext', '')

    map_dir = FILES_DIR / str(map_id)
    map_dir.mkdir(parents=True, exist_ok=True)

    base = sanitize_filename(name) or f"file_{idx}"
    if ext and not base.endswith('.' + ext):
        fname = f"{idx:02d}_{base}.{ext}"
    else:
        fname = f"{idx:02d}_{base}"
    fpath = map_dir / fname

    if fpath.exists() and fpath.stat().st_size > 1024:
        return (map_id, idx, True, fpath.stat().st_size)

    last_error = None
    for attempt in range(RETRIES):
        try:
            r = session.get(url, timeout=120, stream=True)
            if r.status_code != 200:
                last_error = f"HTTP {r.status_code}"
                if attempt < RETRIES - 1:
                    time.sleep(RETRY_BACKOFF[attempt])
                continue
            total = 0
            with open(fpath, 'wb') as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)
            return (map_id, idx, True, total)
        except Exception as e:
            last_error = str(e)
            if attempt < RETRIES - 1:
                time.sleep(RETRY_BACKOFF[attempt])

    print(f"  Error [{map_id}/{idx}]: {last_error}", file=sys.stderr)
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

    total_bytes = 0
    completed_maps = set(already_done)
    completed_files = 0
    consec_fails = 0
    cooldown = 0
    queue_idx = 0
    total = len(remaining)
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {}
        while queue_idx < total or futures:
            while len(futures) < MAX_WORKERS and queue_idx < total:
                if cooldown > 0:
                    print(f"  ⏳ Cooldown {cooldown}s at {completed_files}/{total}...", file=sys.stderr)
                    time.sleep(cooldown)
                    cooldown = max(0, cooldown - 30)
                time.sleep(REQUEST_DELAY / MAX_WORKERS)
                mid, finfo, idx = remaining[queue_idx]
                fut = pool.submit(download_file, mid, finfo, idx)
                futures[fut] = (mid, idx)
                queue_idx += 1

            if not futures:
                break
            done, _ = concurrent.futures.wait(futures, return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in done:
                mid, idx = futures.pop(fut)
                rmid, ridx, success, bcount = fut.result()
                if success:
                    total_bytes += bcount
                    completed_files += 1
                    completed_maps.add(mid)
                    consec_fails = max(0, consec_fails - 1)
                    if cooldown > 0 and consec_fails == 0:
                        cooldown = max(0, cooldown - 10)
                else:
                    consec_fails += 1
                    if consec_fails >= 3:
                        new_cd = min(cooldown + 120, 600)
                        if new_cd > cooldown:
                            cooldown = new_cd
                            print(f"  ⚠ Cooldown raised to {cooldown}s ({consec_fails} consecutive failures)", file=sys.stderr)
                        consec_fails = 0
            mb = total_bytes / 1048576
            elapsed = time.time() - start_time
            rate = completed_files / elapsed if elapsed > 0 else 0
            eta = (total - completed_files) / rate if rate > 0 else 0
            if completed_files and completed_files % 10 == 0:
                print(f"  [{completed_files}/{total}] {mb:.0f} MB | {rate:.1f} f/s | ETA {eta/60:.0f}min", file=sys.stderr)
            if completed_files % 50 == 0:
                with open(MANIFEST_FILE, 'w') as f:
                    json.dump(list(completed_maps), f)

    with open(MANIFEST_FILE, 'w') as f:
        json.dump(list(completed_maps), f)

    print(f"\nDone: {completed_files} files downloaded", file=sys.stderr)
    print(f"Total: {total_bytes / 1073741824:.1f} GB", file=sys.stderr)


if __name__ == '__main__':
    main()
