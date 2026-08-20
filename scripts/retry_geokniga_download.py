#!/usr/bin/env python3
"""Periodically resume GeoKniga file downloads. Runs every hour if blocked."""

import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

SCRIPT = Path(__file__).resolve().parent / "download_geokniga_files.py"
LOG = Path("/tmp/geokniga_download.log")
TRIES_BEFORE_LONG_WAIT = 3


def is_geokniga_available():
    import urllib.request
    try:
        req = urllib.request.Request(
            "https://www.geokniga.org/maps/1025",
            headers={'User-Agent': 'Mozilla/5.0'},
            method='HEAD',
        )
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status == 200
    except Exception:
        return False


def main():
    failures = 0
    while True:
        available = is_geokniga_available()
        now = datetime.now().strftime("%H:%M:%S")
        if not available:
            failures += 1
            wait = 60 if failures < TRIES_BEFORE_LONG_WAIT else 300
            print(f"[{now}] GeoKniga недоступен (попытка {failures}), жду {wait} мин...")
            time.sleep(wait * 60)
            continue

        failures = 0
        print(f"[{now}] GeoKniga доступен! Запускаю загрузчик...")
        log_entry = f"\n=== {now} ==="
        with open(LOG, 'a') as f:
            f.write(log_entry + "\n")

        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True,
        )
        with open(LOG, 'a') as f:
            f.write(result.stdout + result.stderr + "\n")

        print(f"  Done, жду 10 мин до следующей проверки...")
        time.sleep(600)


if __name__ == '__main__':
    main()
