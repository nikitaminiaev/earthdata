#!/usr/bin/env python3
"""Wait for the file scraper to finish, then launch the file downloader."""

import time
import subprocess
import sys
from pathlib import Path

VECS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"
FILES_DIR = Path(__file__).resolve().parent.parent / "data" / "geokniga_files"
DOWNLOAD_SCRIPT = Path(__file__).resolve().parent / "download_geokniga_files.py"
CATALOG_FILE = VECS_DIR / "geokniga_catalog_files.geojson"


def main():
    print("Waiting for file scraper to finish...")

    while True:
        if not CATALOG_FILE.exists():
            time.sleep(10)
            continue

        import json
        with open(CATALOG_FILE) as f:
            fc = json.load(f)

        # Check if all maps have been processed (have files_count key)
        total = len(fc['features'])
        processed = sum(1 for f in fc['features'] if 'files_count' in f['properties'])
        print(f"  Scraper progress: {processed}/{total}")

        if processed >= total:
            print("File scraper complete! Starting downloader...")
            break

        time.sleep(10)

    # Launch downloader
    result = subprocess.run(
        [sys.executable, str(DOWNLOAD_SCRIPT)],
        capture_output=False,
    )
    sys.exit(result.returncode)


if __name__ == '__main__':
    main()
