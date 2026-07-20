#!/usr/bin/env python3
"""
Download OSM raster tiles into MBTiles for offline use.

Usage:
  python3 download_offline_tiles.py [--bbox lon_min lat_min lon_max lat_max] [--zooms 2-12] [--output path]

Presets:
  --preset russia    Russia + Central Asia + Europe (default)
  --preset world     Whole world at z2-8 only (small)
"""

import argparse, math, os, sys, time, json, sqlite3, hashlib
from pathlib import Path
from urllib.request import urlopen, Request
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Thread-local SQLite connections
_tls = threading.local()

USER_AGENT = "curl/8.0"  # OSM blocks browser-like UAs, curl works
DELAY = 0.05  # seconds between requests per thread
MAX_WORKERS = 4
MAX_RETRIES = 2

# Known blocked/placeholder tile MD5 hashes (OSM 403 response)
BLOCKED_HASHES = {
    "c069a15b2cc2d6b6f527ad09eb93c61a",  # OSM "Access blocked" tile (no UA)
    "0ca3dfb6df8f39994a6a2e69127dbf47",  # OSM blocked tile (browser UA)
}

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "tiles"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def osm_to_tms(y, z):
    return (1 << z) - 1 - y


def tile_bounds(lon_min, lat_min, lon_max, lat_max, z):
    """Return (x_min, x_max, y_min, y_max) in OSM tile coords."""
    x_min = int((lon_min + 180) / 360 * (1 << z))
    x_max = int((lon_max + 180) / 360 * (1 << z))
    y_min = int((1 - math.log(math.tan(math.radians(lat_max)) + 1 / math.cos(math.radians(lat_max))) / math.pi) / 2 * (1 << z))
    y_max = int((1 - math.log(math.tan(math.radians(lat_min)) + 1 / math.cos(math.radians(lat_min))) / math.pi) / 2 * (1 << z))
    return max(0, x_min), min((1 << z) - 1, x_max), max(0, y_min), min((1 << z) - 1, y_max)


def is_blocked_tile(data: bytes) -> bool:
    return hashlib.md5(data).hexdigest() in BLOCKED_HASHES


def get_db_conn(db_path: str):
    if not hasattr(_tls, "conn"):
        _tls.conn = sqlite3.connect(str(db_path))
        _tls.conn.execute("PRAGMA synchronous = OFF")
        _tls.conn.execute("PRAGMA journal_mode = MEMORY")
        _tls.conn.execute("PRAGMA cache_size = -64000")
    return _tls.conn


def download_tile(z, x, y, db_path, lock, stats):
    url = f"https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    tms_y = osm_to_tms(y, z)

    for attempt in range(MAX_RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT})
            resp = urlopen(req, timeout=15)
            data = resp.read()
            if len(data) < 50:
                raise ValueError(f"Tile too small ({len(data)} bytes)")
            if is_blocked_tile(data):
                raise ValueError(f"Tile blocked ({hashlib.md5(data).hexdigest()[:12]}...)")
            break
        except Exception as e:
            if attempt < MAX_RETRIES:
                time.sleep(DELAY * 3)
                continue
            with lock:
                stats["errors"] += 1
                if stats["errors"] <= 5:
                    print(f"  Error {url}: {e}")
            return

    time.sleep(DELAY)

    conn = get_db_conn(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO tiles (zoom_level, tile_column, tile_row, tile_data) VALUES (?, ?, ?, ?)",
        (z, x, tms_y, data),
    )
    conn.commit()

    with lock:
        stats["downloaded"] += 1
        if stats["downloaded"] % 200 == 0:
            pct = stats["downloaded"] / stats["total"] * 100 if stats["total"] else 0
            elapsed = time.time() - stats["start"]
            rate = stats["downloaded"] / elapsed if elapsed > 0 else 0
            remaining = (stats["total"] - stats["downloaded"]) / rate if rate > 0 else 0
            print(f"  [{stats['downloaded']}/{stats['total']}] {pct:.1f}% | {rate:.1f} tiles/s | ETA {remaining:.0f}s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bbox", type=float, nargs=4, help="lon_min lat_min lon_max lat_max")
    parser.add_argument("--zooms", default="2-12", help="Zoom range (e.g. 2-10)")
    parser.add_argument("--output", default=str(OUTPUT_DIR / "osm_offline.mbtiles"))
    parser.add_argument("--preset", choices=["russia", "world"], default="russia")
    args = parser.parse_args()

    if args.preset == "russia" and not args.bbox:
        # Russia + Europe + Central Asia
        args.bbox = (-10, 35, 180, 72)
    elif args.preset == "world" and not args.bbox:
        args.bbox = (-180, -85, 180, 85)

    min_zoom, max_zoom = map(int, args.zooms.split("-"))
    lon_min, lat_min, lon_max, lat_max = args.bbox

    print(f"Bounding box: {args.bbox}")
    print(f"Zoom range: {min_zoom}-{max_zoom}")
    print(f"Output: {args.output}")

    out_db_path = Path(args.output)
    out_db_path.parent.mkdir(parents=True, exist_ok=True)

    # estimate tiles
    total_tiles = 0
    tile_list = []
    for z in range(min_zoom, max_zoom + 1):
        x_min, x_max, y_min, y_max = tile_bounds(lon_min, lat_min, lon_max, lat_max, z)
        tiles_at_z = (x_max - x_min + 1) * (y_max - y_min + 1)
        total_tiles += tiles_at_z
        print(f"  z{z}: x[{x_min}-{x_max}] y[{y_min}-{y_max}] = {tiles_at_z} tiles")
        for x in range(x_min, x_max + 1):
            for y in range(y_min, y_max + 1):
                tile_list.append((z, x, y))

    print(f"\nTotal tiles to download: {total_tiles}")
    est_size_mb = total_tiles * 0.015  # ~15 KB per tile
    print(f"Estimated size: {est_size_mb:.0f} MB")

    # create MBTiles DB
    conn = sqlite3.connect(str(out_db_path))
    conn.execute("CREATE TABLE IF NOT EXISTS tiles (zoom_level INTEGER, tile_column INTEGER, tile_row INTEGER, tile_data BLOB)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tiles ON tiles(zoom_level, tile_column, tile_row)")
    conn.execute("CREATE TABLE IF NOT EXISTS metadata (name TEXT, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('name', 'Earthdata Offline Map')")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('format', 'png')")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('type', 'baselayer')")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('version', '1.0')")
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('description', 'OSM tiles for offline use')")
    conn.commit()

    # filter out already downloaded with valid content
    existing = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    print(f"Already in DB: {existing} tiles")
    print("Checking existing tiles for blocked content...")

    blocked_lookup = {}
    batch_size = 10000
    offset = 0
    while True:
        rows = conn.execute(
            "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles LIMIT ? OFFSET ?",
            (batch_size, offset),
        ).fetchall()
        if not rows:
            break
        for z, x, tms_y, data in rows:
            if is_blocked_tile(data):
                blocked_lookup[(z, x, tms_y)] = True
        offset += len(rows)
        print(f"  Checked {offset}/{existing}...")

    to_download = []
    valid_skip = 0
    for z, x, y in tile_list:
        tms_y = osm_to_tms(y, z)
        if (z, x, tms_y) in blocked_lookup:
            to_download.append((z, x, y))
        elif conn.execute(
            "SELECT 1 FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y),
        ).fetchone() is None:
            to_download.append((z, x, y))
        else:
            valid_skip += 1

    recheck_count = len(to_download)
    print(f"To download: {len(to_download)} ({valid_skip} valid cached, {recheck_count} to re-download)")

    stats = {"downloaded": 0, "errors": 0, "total": len(to_download), "start": time.time()}
    lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(download_tile, z, x, y, out_db_path, lock, stats) for z, x, y in to_download]
        for f in as_completed(futures):
            pass

    # final count
    final = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('count', ?)", (str(final),))
    conn.commit()
    conn.close()

    print(f"\nDone. {final} tiles in {out_db_path}")
    size_mb = out_db_path.stat().st_size / 1024 / 1024
    print(f"DB size: {size_mb:.0f} MB")
    print(f"Errors: {stats['errors']}")


if __name__ == "__main__":
    main()
