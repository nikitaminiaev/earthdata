#!/bin/bash
LOG="/mnt/hdd2/Earthdata/10_server/data/tiles/download_log.txt"
MBTILES="/mnt/hdd2/Earthdata/10_server/data/tiles/osm_offline.mbtiles"
TARGET=1677452
SCRIPTS="/mnt/hdd2/Earthdata/10_server/scripts"

echo "[$(date '+%H:%M:%S')] Waiting 8h before checking Z12 Russia+Europe..." >> "$LOG"
sleep 28800  # 8 hours

echo "[$(date '+%H:%M:%S')] Checking Z12 count..." >> "$LOG"
COUNT=$(sqlite3 "$MBTILES" "SELECT COUNT(*) FROM tiles WHERE zoom_level=12;" 2>/dev/null)
echo "[$(date '+%H:%M:%S')] Z12 tiles: $COUNT / $TARGET" >> "$LOG"

if [ "$COUNT" -ge "$TARGET" ]; then
    echo "[$(date '+%H:%M:%S')] Z12 Russia+Europe complete! Starting WORLD z12..." >> "$LOG"
    python3 -u "$SCRIPTS/download_offline_tiles.py" --preset world --zooms 12-12 >> "$LOG" 2>&1
    echo "[$(date '+%H:%M:%S')] WORLD z12 finished!" >> "$LOG"
else
    echo "[$(date '+%H:%M:%S')] NOT complete ($COUNT/$TARGET). Waiting another 4h..." >> "$LOG"
    sleep 14400
    COUNT2=$(sqlite3 "$MBTILES" "SELECT COUNT(*) FROM tiles WHERE zoom_level=12;" 2>/dev/null)
    echo "[$(date '+%H:%M:%S')] Z12 tiles after extra wait: $COUNT2 / $TARGET" >> "$LOG"
    if [ "$COUNT2" -ge "$TARGET" ]; then
        echo "[$(date '+%H:%M:%S')] Starting WORLD z12..." >> "$LOG"
        python3 -u "$SCRIPTS/download_offline_tiles.py" --preset world --zooms 12-12 >> "$LOG" 2>&1
        echo "[$(date '+%H:%M:%S')] WORLD z12 finished!" >> "$LOG"
    else
        echo "[$(date '+%H:%M:%S')] Still not done ($COUNT2/$TARGET). Check manually." >> "$LOG"
    fi
fi
