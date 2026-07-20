#!/bin/bash
set -e
ASTER_DIR="/mnt/hdd2/Earthdata/03_aster"
DST_DIR="/mnt/hdd2/Earthdata/10_server/data/aster"
mkdir -p "$DST_DIR"

echo "[$(date)] Building ASTER VRT..."
find "$ASTER_DIR" -maxdepth 1 -name "*.tif" -type f > /tmp/aster_files.txt
wc -l < /tmp/aster_files.txt

gdalbuildvrt -overwrite -input_file_list /tmp/aster_files.txt "$DST_DIR/aster_dem.vrt"

echo "[$(date)] Done."
ls -lh "$DST_DIR/aster_dem.vrt"
