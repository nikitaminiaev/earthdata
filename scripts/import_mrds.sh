#!/bin/bash
set -e
MRDS_CSV="/mnt/hdd2/Earthdata/04_mrds/mrds.csv"
DST_DIR="/mnt/hdd2/Earthdata/10_server/data/vectors"
DB="$DST_DIR/earth_vectors.sqlite"
mkdir -p "$DST_DIR"

echo "[$(date)] Importing MRDS..."
rm -f "$DB"

# Create SpatiaLite with MRDS
ogr2ogr -f SQLite "$DB" "$MRDS_CSV" -nln mrds \
  -lco SPATIALITE=YES -lco GEOMETRY_NAME=geom \
  -oo X_POSSIBLE_NAMES=longitude -oo Y_POSSIBLE_NAMES=latitude \
  -oo KEEP_GEOM_COLUMNS=NO \
  -s_srs EPSG:4326 -t_srs EPSG:4326 \
  -lco SRID=4326

echo "MRDS: $(ogrinfo -q -al -so "$DB" mrds 2>&1 | grep -c 'Feature Count') deposits"
echo "[$(date)] Done."
