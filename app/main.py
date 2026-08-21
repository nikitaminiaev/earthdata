import json
import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from app.config import GEBCO_PATH, ASTER_VRT, VECTORS_DB, MRDS_GEOJSON, GEOLOGY_DB, OSM_MBTILES, NRAD_CCM_COG, AUS_TERNARY_COG

logger = logging.getLogger("earthdata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SPATIALITE_PATH = "/usr/lib/aarch64-linux-gnu/mod_spatialite"

app = FastAPI(title="Earthdata Server")

from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@contextmanager
def spatial_conn(db_path: str):
    conn = sqlite3.connect(str(db_path))
    try:
        conn.enable_load_extension(True)
        conn.load_extension(SPATIALITE_PATH)
        yield conn
    finally:
        conn.close()

# ---- Preload data caches (loaded once at startup) ----

_mrds_cache = None

def _get_mrds():
    global _mrds_cache
    if _mrds_cache is None:
        p = Path(MRDS_GEOJSON)
        if p.exists():
            with open(p) as f:
                _mrds_cache = json.load(f)
    return _mrds_cache

_geojson_cache = {}

def _get_geojson(name: str):
    if name not in _geojson_cache:
        p = VECS_DIR / f"{name}.geojson"
        if p.exists():
            _geojson_cache[name] = p.read_bytes()
        else:
            _geojson_cache[name] = None
    return _geojson_cache[name]

_catalog_cache = None
_catalog_files_cache = None

def _get_geokniga_catalog():
    global _catalog_cache, _catalog_files_cache
    files_p = VECS_DIR / "geokniga_catalog_files.geojson"
    basic_p = VECS_DIR / "geokniga_catalog.geojson"
    if files_p.exists():
        if _catalog_files_cache is None:
            with open(files_p) as f:
                _catalog_files_cache = json.load(f)
        return _catalog_files_cache
    if _catalog_cache is None:
        with open(basic_p) as f:
            _catalog_cache = json.load(f)
    return _catalog_cache

def get_best_elevation_source():
    for p in [GEBCO_PATH, ASTER_VRT]:
        if Path(p).exists():
            return p
    return None

RGB_RASTER_LAYERS = {
    "nrad_ccm": NRAD_CCM_COG,
    "aus_ternary": AUS_TERNARY_COG,
}

RGB_MIN_ZOOM = {
    "nrad_ccm": 4,
    "aus_ternary": 6,
}

# ---- Static frontend ----
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    from fastapi.responses import HTMLResponse

    @app.get("/")
    async def index():
        return HTMLResponse((FRONTEND_DIR / "index.html").read_text())

    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR), html=False), name="frontend")

@app.get("/test-tile")
async def test_tile():
    import base64, sqlite3
    conn = sqlite3.connect(OSM_MBTILES)
    tiles = []
    for z, x, y_xyz in [(10, 619, 320), (4, 7, 3), (4, 7, 4), (4, 8, 3), (4, 8, 4), (5, 15, 6), (5, 15, 7), (5, 16, 6), (5, 16, 7)]:
        tms_y = (1 << z) - 1 - y_xyz
        data = conn.execute("SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?", (z, x, tms_y)).fetchone()
        if data:
            tiles.append((z, x, y_xyz, base64.b64encode(data[0]).decode()))
    conn.close()
    base64_tiles = ""
    for z, x, y, b64 in tiles:
        base64_tiles += '<div style="display:inline-block;margin:4px;text-align:center"><b>z%d x%d y%d</b><br><img src="data:image/png;base64,%s" width="128" height="128"></div>' % (z, x, y, b64)
    return HTMLResponse("""<html><body style="font-family:sans-serif">
<h2>Base64 inline tiles (no HTTP)</h2>
""" + base64_tiles + """
<hr><h2>Regular HTTP tiles</h2>
<div id="status">Status: waiting...</div>
<script>
var urls = [
  '/api/osm-tiles/4/7/3.png',
  '/api/osm-tiles/4/7/4.png',
  '/api/osm-tiles/4/8/3.png',
  '/api/osm-tiles/4/8/4.png',
  '/api/osm-tiles/5/15/6.png',
  '/api/osm-tiles/5/15/7.png',
  '/api/osm-tiles/5/16/6.png',
  '/api/osm-tiles/5/16/7.png',
];
var status = document.getElementById('status');
urls.forEach(function(url) {
  var img = new Image();
  img.style.width = '128px';
  img.style.height = '128px';
  img.style.border = '1px solid #ccc';
  img.onload = function() { status.textContent = 'OK: ' + url; };
  img.onerror = function() { status.textContent = 'ERROR: ' + url + ' - check console (F12)'; };
  document.body.appendChild(img);
});
</script>
</body></html>""")


# ---- Tile endpoint (simple PNG tiles from COG/VRT) ----

@app.get("/api/tiles/{z}/{x}/{y}.png")
async def tile(z: int, x: int, y: int, layer: str = Query("elevation")):
    import mercantile
    import rasterio
    import numpy as np
    from PIL import Image
    import io

    TILE_SIZE = 256
    b = mercantile.bounds(x, y, z)

    if layer in RGB_RASTER_LAYERS:
        min_z = RGB_MIN_ZOOM.get(layer, 5)
        if z < min_z:
            return transparent_tile()

        src_path = RGB_RASTER_LAYERS[layer]
        if not Path(src_path).exists():
            return Response(status_code=404)
        try:
            with rasterio.open(src_path) as src:
                window = src.window(b.west, b.south, b.east, b.north)
                if window.width < 1 or window.height < 1:
                    return transparent_tile()
                window = window.round_lengths().round_offsets()
                window = rasterio.windows.Window(
                    max(0, int(window.col_off)), max(0, int(window.row_off)),
                    min(int(window.width), src.width), min(int(window.height), src.height),
                )
                if window.width == 0 or window.height == 0:
                    return transparent_tile()
                data = src.read(window=window, boundless=True, fill_value=0,
                                out_shape=(src.count, TILE_SIZE, TILE_SIZE))
                rgb = data[:3].transpose(1, 2, 0).astype(np.uint8)
                mask = (rgb.sum(axis=2) == 0)
                if mask.all():
                    return transparent_tile()
                rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
                rgba[:, :, :3] = rgb
                rgba[:, :, 3] = (~mask).astype(np.uint8) * 255
                img = Image.fromarray(rgba, mode="RGBA")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return Response(content=buf.getvalue(), media_type="image/png")
        except Exception:
            return transparent_tile()

    src_path = get_best_elevation_source()
    if not src_path:
        return Response(status_code=404)

    try:
        with rasterio.open(src_path) as src:
            window = src.window(b.west, b.south, b.east, b.north)

            if window.width < 1 or window.height < 1:
                return empty_tile()

            window = window.round_lengths().round_offsets()
            window = rasterio.windows.Window(
                max(0, int(window.col_off)), max(0, int(window.row_off)),
                min(int(window.width), src.width), min(int(window.height), src.height),
            )

            data = src.read(1, window=window, boundless=True, fill_value=src.nodata or -32768)
            data = data.astype(np.float32)
            data[data == (src.nodata or -32768)] = np.nan

            height, width = data.shape
            if width == 0 or height == 0:
                return empty_tile()

            norm = np.clip((data - np.nanmin(data)) / (np.nanmax(data) - np.nanmin(data) + 1e-10) * 255, 0, 255)
            norm = np.nan_to_num(norm, nan=0).astype(np.uint8)

            img = Image.fromarray(norm, mode="L")
            if width != TILE_SIZE or height != TILE_SIZE:
                resample = Image.NEAREST if width < TILE_SIZE else Image.BILINEAR
                img = img.resize((TILE_SIZE, TILE_SIZE), resample)

            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

    except Exception:
        return empty_tile()


def tile_bounds(z, x, y):
    import mercantile
    b = mercantile.bounds(x, y, z)
    return (b.west, b.south, b.east, b.north)


def tile_count():
    try:
        conn = sqlite3.connect(OSM_MBTILES, timeout=5)
        count = conn.execute("SELECT MAX(rowid) FROM tiles").fetchone()[0]
        conn.close()
        return count
    except Exception:
        return -1


def empty_tile(color=0):
    from PIL import Image
    import io
    img = Image.new("L", (256, 256), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")

def transparent_tile():
    from PIL import Image
    import io
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


# ---- Offline OSM tile endpoint (from MBTiles) ----

_osm_db = None

def _get_osm_db():
    global _osm_db
    if _osm_db is None:
        _osm_db = sqlite3.connect(OSM_MBTILES)
        _osm_db.execute("PRAGMA query_only = 1")
        _osm_db.execute("PRAGMA journal_mode = WAL")
        _osm_db.execute("PRAGMA cache_size = -32000")
    return _osm_db

@app.get("/api/osm-tiles/{z}/{x}/{y}.png")
async def osm_tile(z: int, x: int, y: int):
    if not Path(OSM_MBTILES).exists():
        return empty_tile(220)

    tms_y = (1 << z) - 1 - y

    try:
        conn = _get_osm_db()
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y),
        ).fetchone()
        if row:
            return Response(content=row[0], media_type="image/png")
        return empty_tile(220)
    except Exception as e:
        logger.error("osm_tile error: %s", e)
        return empty_tile(220)


# ---- Point query: what's at this location? ----

@app.get("/api/query")
async def query_point(
    lat: float = Query(...),
    lon: float = Query(...),
):
    result = {"coords": {"lat": lat, "lon": lon}}

    # Elevation from best source
    src_path = get_best_elevation_source()
    if src_path:
        try:
            import rasterio
            with rasterio.open(src_path) as src:
                for sub in [1]:
                    vals = list(src.sample([(lon, lat)]))
                    if vals:
                        v = float(vals[0][0])
                        if v < -10000 or v > 10000:
                            v = None
                        result["elevation_m"] = v
        except Exception:
            result["elevation_m"] = None
    else:
        result["elevation_m"] = None

    # MRDS: nearest deposits
    fc = _get_mrds()
    if fc:
        try:
            nearby = []
            for feat in fc["features"]:
                lng, ltd = feat["geometry"]["coordinates"]
                d = ((ltd - lat) ** 2 + (lng - lon) ** 2) ** 0.5
                if d < 2.0:
                    feat["properties"]["dist_deg"] = round(d, 4)
                    nearby.append(feat["properties"])
            nearby.sort(key=lambda x: x.get("dist_deg", 999))
            result["mineral_deposits"] = nearby[:20]
        except Exception:
            result["mineral_deposits"] = []

    # Geology at point
    if Path(GEOLOGY_DB).exists():
        try:
            with spatial_conn(GEOLOGY_DB) as conn:
                row = conn.execute(
                    "SELECT AGERA, RXTP, NAME FROM geology WHERE ST_Intersects(geom, MakePoint(?, ?, 4326)) LIMIT 1",
                    (lon, lat),
                ).fetchone()
            if row:
                result["geology"] = {
                    "age_era": row[0],
                    "rock_type": row[1],
                    "name": row[2],
                }
        except Exception:
            result["geology"] = None
    else:
        result["geology"] = None

    # Groundwater at point
    if Path(VECTORS_DB).exists():
        try:
            with spatial_conn(VECTORS_DB) as conn:
                tables = ["gw_tba", "gw_aquifertype", "gw_depthtogw", "gw_salinity"]
                for t in tables:
                    try:
                        desc = conn.execute(f"PRAGMA table_info({t})").fetchall()
                        cols = [d[1] for d in desc if d[1] != "geom"]
                        text_cols = ",".join(f'"{c}"' for c in cols)
                        row = conn.execute(
                            f"SELECT {text_cols} FROM {t} WHERE ST_Intersects(geom, MakePoint(?, ?, 4326)) LIMIT 1",
                            (lon, lat),
                        ).fetchone()
                        if row:
                            result[f"gw_{t[3:]}"] = dict(zip(cols, row))
                    except Exception:
                        pass
        except Exception:
            pass

    return result


# ---- MRDS search ----

@app.get("/api/search")
async def search_mineral(
    commodity: str = Query(...),
    limit: int = Query(50, le=500),
):
    fc = _get_mrds()
    if fc is None:
        raise HTTPException(404, "MRDS data not available")

    results = []
    for feat in fc["features"]:
        props = feat["properties"]
        if any(commodity.upper() in str(props.get(k, "")).upper() for k in ["c1", "c2", "name"]):
            results.append({
                "name": props.get("name", ""),
                "commodity": props.get("c1", ""),
                "country": props.get("ct", ""),
                "status": props.get("st", ""),
                "lon": feat["geometry"]["coordinates"][0],
                "lat": feat["geometry"]["coordinates"][1],
            })
            if len(results) >= limit:
                break

    return {"query": commodity, "results": results, "count": len(results)}


# ---- Groundwater at point ----

@app.get("/api/groundwater")
async def groundwater_at_point(
    lat: float = Query(...),
    lon: float = Query(...),
):
    if not Path(VECTORS_DB).exists():
        return {"error": "no groundwater data"}
    result = {}
    tables = ["gw_tba", "gw_aquifertype", "gw_depthtogw", "gw_salinity"]
    try:
        with spatial_conn(VECTORS_DB) as conn:
            for t in tables:
                try:
                    desc = conn.execute(f"PRAGMA table_info({t})").fetchall()
                    cols = [d[1] for d in desc if d[1] != "geom"]
                    text_cols = ",".join(f'"{c}"' for c in cols)
                    row = conn.execute(
                        f"SELECT {text_cols} FROM {t} WHERE ST_Intersects(geom, MakePoint(?, ?, 4326)) LIMIT 1",
                        (lon, lat),
                    ).fetchone()
                    if row:
                        result[t[3:]] = dict(zip(cols, row))
                except Exception:
                    pass
    except Exception:
        pass
    return result


# ---- Vector layer endpoints (for frontend map) ----

VECS_DIR = Path(__file__).resolve().parent.parent / "data" / "vectors"

def serve_geojson(name: str):
    async def _handler():
        data = _get_geojson(name)
        if data is None:
            raise HTTPException(404)
        return Response(content=data, media_type="application/geo+json")
    return _handler

app.get("/api/mrds-layer")(serve_geojson("mrds"))
app.get("/api/geology-layer")(serve_geojson("geology"))
app.get("/api/gw-aquifertype")(serve_geojson("gw_aquifertype"))
app.get("/api/gw-depth")(serve_geojson("gw_depthtogw"))
app.get("/api/gw-salinity")(serve_geojson("gw_salinity"))
app.get("/api/gw-tba")(serve_geojson("gw_tba"))
app.get("/api/osm-mining")(serve_geojson("osm_mining_enriched"))
app.get("/api/rosnedra")(serve_geojson("rosnedra_plots"))
def serve_geokniga_catalog():
    async def _handler():
        fc = _get_geokniga_catalog()
        if fc is None:
            raise HTTPException(404)
        return Response(content=json.dumps(fc), media_type="application/geo+json")
    return _handler

app.get("/api/geokniga-catalog")(serve_geokniga_catalog())

GEOKNIGA_FILES = Path(__file__).resolve().parent.parent / "data" / "geokniga_files"

@app.get("/api/geokniga-file/{map_id}/{idx}")
async def geokniga_file(map_id: int, idx: int, check: bool = False):
    fc = _get_geokniga_catalog()
    if fc is None:
        raise HTTPException(404, "No enriched catalog")

    target_feat = None
    for feat in fc['features']:
        if feat['properties'].get('id') == map_id:
            target_feat = feat
            break

    if not target_feat:
        raise HTTPException(404, f"Map {map_id} not found")

    files = target_feat['properties'].get('files', [])
    if idx < 0 or idx >= len(files):
        raise HTTPException(404, f"File index {idx} out of range")

    finfo = files[idx]
    name = finfo.get('name', f'file_{idx}')
    ext = finfo.get('ext', '')

    sanitized = name.strip().replace('/', '_').replace('\\', '_')
    sanitized = ''.join(c for c in sanitized if c.isprintable() and c not in '<>:"|?*')
    if ext and not sanitized.endswith('.' + ext):
        fname = f"{idx:02d}_{sanitized}.{ext}"
    else:
        fname = f"{idx:02d}_{sanitized}"

    fpath = GEOKNIGA_FILES / str(map_id) / fname

    if not fpath.exists():
        raise HTTPException(404, "File not downloaded yet")

    if check:
        return Response(status_code=200)

    media_type = {
        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'gif': 'image/gif', 'pdf': 'application/pdf',
        'tif': 'image/tiff', 'tiff': 'image/tiff',
    }.get(ext, 'application/octet-stream')

    return Response(content=fpath.read_bytes(), media_type=media_type)


# ---- Health check ----

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "gebco_cog": Path(GEBCO_PATH).exists(),
        "aster_vrt": Path(ASTER_VRT).exists(),
        "vectors_db": Path(VECTORS_DB).exists(),
        "geology_db": Path(GEOLOGY_DB).exists(),
        "mrds_geojson": Path(MRDS_GEOJSON).exists(),
        "offline_tiles": Path(OSM_MBTILES).exists(),
        "nrad_ccm": Path(NRAD_CCM_COG).exists(),
        "aus_ternary": Path(AUS_TERNARY_COG).exists(),
        "tile_count": tile_count(),
        "tile_total_estimated": 141171,
    }
