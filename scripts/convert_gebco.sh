#!/bin/bash
set -e
SRC="/mnt/hdd2/Earthdata/02_gebco/GEBCO_2026.nc"
DST="/mnt/hdd2/Earthdata/10_server/data/gebco_2026_cog.tif"
mkdir -p "$(dirname "$DST")"

echo "[$(date)] Converting GEBCO NetCDF → COG..."
gdal_translate "$SRC" "$DST" \
  -of COG \
  -co COMPRESS=DEFLATE \
  -co PREDICTOR=2 \
  -co BLOCKSIZE=512 \
  -co BIGTIFF=YES \
  -co RESAMPLING=AVERAGE \
  -co NUM_THREADS=ALL_CPUS \
  -co LEVEL=9 \
  --config GDAL_NUM_THREADS ALL_CPUS

echo "[$(date)] Done."
echo "Result:"
ls -lh "$DST"
