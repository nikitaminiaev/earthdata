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
import socket

# This machine has no IPv6 route. DNS returns AAAA records first, so urllib
# hangs ~5s on each IPv6 connect attempt and errors "Network is unreachable".
# Force IPv4 lookups and cache results so the hot path never re-queries DNS
# (the resolver intermittently fails to return A records under load).
_orig_getaddrinfo = socket.getaddrinfo
_dns_cache = {}


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if family == 0:
        family = socket.AF_INET
    key = (host, port, type, proto, flags)
    cached = _dns_cache.get(key)
    if cached is not None:
        return cached
    last_err = None
    for attempt in range(3):
        try:
            result = _orig_getaddrinfo(host, port, family, type, proto, flags)
            _dns_cache[key] = result
            return result
        except socket.gaierror as e:
            last_err = e
            time.sleep(0.3)
    raise last_err


socket.getaddrinfo = _cached_getaddrinfo

# Pre-warm the tile host DNS cache so downloads never block on lookups
try:
    _cached_getaddrinfo("tile.openstreetmap.org", 443, socket.AF_INET, socket.SOCK_STREAM)
except Exception:
    pass

# Thread-local SQLite connections
_tls = threading.local()

USER_AGENT = "Mozilla/5.0 (X11; Linux armv7l) rv:115.0 Gecko/20100101 Firefox/115.0"
DELAY = 0.1  # seconds between requests per thread
MAX_WORKERS = 6
MAX_RETRIES = 2

# Known blocked/placeholder tile MD5 hashes
BLOCKED_HASHES = {
    "c069a15b2cc2d6b6f527ad09eb93c61a",  # OSM "Access blocked" tile
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
                stats["processed"] += 1
                if stats["processed"] % 500 == 0:
                    pct = stats["downloaded"] / stats["total"] * 100 if stats["total"] else 0
                    elapsed = time.time() - stats["start"]
                    rate = stats["processed"] / elapsed if elapsed > 0 else 0
                    remaining = (stats["total"] - stats["downloaded"]) / rate if rate > 0 else 0
                    print(f"  [{stats['downloaded']}/{stats['total']}] {pct:.1f}% | {rate:.1f} t/s | ETA {remaining:.0f}s | err:{stats['errors']}")
                elif stats["errors"] <= 5:
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
        stats["processed"] += 1
        if stats["processed"] % 500 == 0:
            pct = stats["downloaded"] / stats["total"] * 100 if stats["total"] else 0
            elapsed = time.time() - stats["start"]
            rate = stats["processed"] / elapsed if elapsed > 0 else 0
            remaining = (stats["total"] - stats["downloaded"]) / rate if rate > 0 else 0
            print(f"  [{stats['downloaded']}/{stats['total']}] {pct:.1f}% | {rate:.1f} t/s | ETA {remaining:.0f}s | err:{stats['errors']}")


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

    # Estimate tiles per zoom
    zoom_tile_counts = {}
    total_tiles = 0
    for z in range(min_zoom, max_zoom + 1):
        x_min, x_max, y_min, y_max = tile_bounds(lon_min, lat_min, lon_max, lat_max, z)
        n = (x_max - x_min + 1) * (y_max - y_min + 1)
        zoom_tile_counts[z] = (x_min, x_max, y_min, y_max, n)
        total_tiles += n
        print(f"  z{z}: x[{x_min}-{x_max}] y[{y_min}-{y_max}] = {n:,} tiles")

    print(f"\nTotal: {total_tiles:,} tiles, ~{total_tiles * 0.02:.0f} MB estimated")

    stats = {"downloaded": 0, "errors": 0, "processed": 0, "total": 0, "start": time.time()}
    lock = threading.Lock()

    conn = sqlite3.connect(str(out_db_path))

    # Process one zoom level at a time
    for z in range(min_zoom, max_zoom + 1):
        x_min, x_max, y_min, y_max, tiles_at_z = zoom_tile_counts[z]
        print(f"\n--- Zoom {z} ({tiles_at_z:,} tiles) ---")

        # Load existing tile keys for this zoom
        existing_z = set()
        for row in conn.execute("SELECT tile_column, tile_row FROM tiles WHERE zoom_level=?", (z,)):
            existing_z.add((row[0], row[1]))

        # Count total remaining tiles for this zoom (two passes to estimate max memory)
        # First pass: count only (low memory, just accumulate ints)
        Y_BAND = 50
        band_counts = []  # (band_start, band_end, count)
        for band_start in range(y_min, y_max + 1, Y_BAND):
            band_end = min(band_start + Y_BAND - 1, y_max)
            cnt = 0
            for y in range(band_start, band_end + 1):
                tms_y = osm_to_tms(y, z)
                for x in range(x_min, x_max + 1):
                    if (x, tms_y) not in existing_z:
                        cnt += 1
            if cnt > 0:
                band_counts.append((band_start, band_end, cnt))

        total_remaining = sum(c for _, _, c in band_counts)
        if total_remaining == 0:
            print(f"  All {tiles_at_z:,} tiles already exist, skipping")
            del existing_z
            continue

        stats["total"] += total_remaining
        print(f"  To download: {total_remaining:,} (skipping {tiles_at_z - total_remaining:,})")

        # Second pass: download each band
        for band_start, band_end, _ in band_counts:
            band_tiles = []
            for y in range(band_start, band_end + 1):
                tms_y = osm_to_tms(y, z)
                for x in range(x_min, x_max + 1):
                    if (x, tms_y) not in existing_z:
                        band_tiles.append((z, x, y))

            # Download this band
            BATCH = 5000
            for batch_start in range(0, len(band_tiles), BATCH):
                batch = band_tiles[batch_start:batch_start + BATCH]
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    futures = [pool.submit(download_tile, zz, xx, yy, str(out_db_path), lock, stats)
                              for zz, xx, yy in batch]
                    for f in as_completed(futures):
                        pass

                # Progress from download_tile handles the display

            del band_tiles

        print(f"  Zoom {z} done. Downloaded {total_remaining:,} tiles.")
        del existing_z

    # final count
    conn = sqlite3.connect(str(out_db_path))
    final = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO metadata VALUES ('count', ?)", (str(final),))
    conn.commit()
    conn.close()

    print(f"\nDone. {final} tiles in {out_db_path}")
    size_mb = Path(out_db_path).stat().st_size / 1024 / 1024
    print(f"DB size: {size_mb:.0f} MB")
    print(f"Errors: {stats['errors']}")


if __name__ == "__main__":
    main()
