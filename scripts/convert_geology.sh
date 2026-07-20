#!/bin/bash
set -e

GEOLOGY_DIR="/mnt/hdd2/Earthdata/05_geology/world_geology_1_35M"
DST_DIR="/mnt/hdd2/Earthdata/10_server/data/geology"
mkdir -p "$DST_DIR"

TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

echo "[$(date)] Converting geology E00 → GeoPackage..."

# Rock polygons (ROX)
ROX_FILES=$(ls "$GEOLOGY_DIR/GENGEOL/ARCEXP/WITHATT/ROX"*.E00 2>/dev/null)
FIRST=true

for e00 in $ROX_FILES; do
    CONT=$(basename "$e00" | sed 's/ROX//;s/\.E00//' | tr 'A-Z' 'a-z')
    echo "  Processing ROX_$CONT..."
    COV="$TMPDIR/rox_$CONT"
    GPKG="$DST_DIR/rox_$CONT.gpkg"
    
    avcimport "$e00" "$COV" 2>/dev/null
    ogr2ogr -f GPKG "$GPKG" "$COV" PAL -nln geology -dsco SPATIALITE=YES -lco GEOMETRY_NAME=geom 2>/dev/null
    
    if [ "$FIRST" = true ]; then
        cp "$GPKG" "$DST_DIR/earth_geology.sqlite"
        FIRST=false
    else
        ogr2ogr -f SQLite -append -update "$DST_DIR/earth_geology.sqlite" "$GPKG" -nln geology -lco GEOMETRY_NAME=geom 2>/dev/null
    fi
    echo "  $(ls -lh "$DST_DIR/earth_geology.sqlite" | awk '{print $5}')"
done

# Fault lines (FLT)
echo "[$(date)] Processing faults..."
FLT_FILES=$(ls "$GEOLOGY_DIR/GENGEOL/ARCEXP/WITHATT/FLT"*.E00 2>/dev/null)

for e00 in $FLT_FILES; do
    CONT=$(basename "$e00" | sed 's/FLT//;s/\.E00//' | tr 'A-Z' 'a-z')
    echo "  Processing FLT_$CONT..."
    COV="$TMPDIR/flt_$CONT"
    GPKG="$DST_DIR/flt_$CONT.gpkg"
    
    avcimport "$e00" "$COV" 2>/dev/null
    ogr2ogr -f GPKG "$GPKG" "$COV" ARC -nln faults -dsco SPATIALITE=YES -lco GEOMETRY_NAME=geom 2>/dev/null
    
    if [ -f "$DST_DIR/earth_geology.sqlite" ]; then
        ogr2ogr -f SQLite -append -update "$DST_DIR/earth_geology.sqlite" "$GPKG" -nln faults -lco GEOMETRY_NAME=geom 2>/dev/null
    fi
done

echo "[$(date)] Done. Final DB:"
ls -lh "$DST_DIR/earth_geology.sqlite"
ogrinfo -q -al -so "$DST_DIR/earth_geology.sqlite" geology 2>&1 | head -15
