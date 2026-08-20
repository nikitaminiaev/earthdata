#!/bin/bash
set -e
DIR="/mnt/hdd2/Earthdata/10_server/data/radiometrics"
mkdir -p "$DIR"

log() { echo "[$(date '+%H:%M:%S')] $*"; }

# ========== 1. USGS NArad — Северная Америка ==========
log "=== USGS NArad (4 GeoTIFF) ==="
BASE="https://mrdata.usgs.gov/radiometric/data"
for f in NArad_K_geog83.tif NArad_U_geog83.tif NArad_Th_geog83.tif NArad_exp_geog83.tif; do
  out="$DIR/$f"
  if [ -f "$out" ]; then
    log "  EXISTS $f ($(du -h "$out" | cut -f1))"
  else
    log "  DL $f"
    curl -sL --connect-timeout 30 --max-time 600 -o "$out" "$BASE/$f"
    log "  DONE $f ($(du -h "$out" | cut -f1))"
  fi
done

# ========== 2. Австралия Radmap v4 ==========
log "=== Australia Radmap v4 (GeoTIFF) ==="
AUS_DIR="$DIR/australia"
mkdir -p "$AUS_DIR"
AUS_URL="https://d28rz98at9flks.cloudfront.net/134857/134857_00_0.zip"
AUS_ZIP="$AUS_DIR/radmap_v4_full.zip"
if [ -f "$AUS_DIR/radmap_v4_unpacked.done" ]; then
  log "  EXISTS Radmap v4 (unpacked)"
else
  if [ ! -f "$AUS_ZIP" ]; then
    log "  DL Radmap v4 (3.1 GB)..."
    curl -sL --connect-timeout 30 --max-time 7200 -o "$AUS_ZIP" "$AUS_URL"
    log "  DONE Radmap v4 zip ($(du -h "$AUS_ZIP" | cut -f1))"
  fi
  log "  Unpacking..."
  unzip -o -q "$AUS_ZIP" -d "$AUS_DIR"
  touch "$AUS_DIR/radmap_v4_unpacked.done"
  log "  Unpacked"
fi

# ========== 3. Канада (TODO — portal migrated) ==========
log "=== Canada — SKIP (portal geophysical-data.canada.ca migrated, no direct links) ==="
log "  TODO: download via WMS — http://wms.agg.nrcan.gc.ca/wms2/wms2.aspx"

log "=== ALL DONE ==="
echo ""
du -sh "$DIR"/*.tif "$DIR"/australia/ 2>/dev/null || true
