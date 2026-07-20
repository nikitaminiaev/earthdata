#!/bin/bash
set -e
GW_DIR="/mnt/hdd2/Earthdata/06_groundwater"
DB="/mnt/hdd2/Earthdata/10_server/data/vectors/earth_vectors.sqlite"

echo "[$(date)] Importing groundwater layers..."

for d in igrac_aquifertype igrac_depthtogw igrac_salinity igrac_tba; do
    SHP=$(find "$GW_DIR/$d" -name "*.shp" 2>/dev/null | head -1)
    [ -z "$SHP" ] && echo "  $d: no shapefile" && continue
    LAYER="${d#igrac_}"
    echo "  $LAYER..."
    ogr2ogr -f SQLite -append -update "$DB" "$SHP" \
      -nln "gw_$LAYER" -lco GEOMETRY_NAME=geom -lco SRID=4326
done

echo "[$(date)] Done."
ogrinfo -q -al -so "$DB" 2>&1 | grep -E "^[0-9]:" | head -20
