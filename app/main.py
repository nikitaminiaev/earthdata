import json
import sqlite3
import logging
from pathlib import Path
from contextlib import contextmanager
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from app.config import GEBCO_PATH, ASTER_VRT, VECTORS_DB, MRDS_GEOJSON, GEOLOGY_DB, OSM_MBTILES

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

# ---- Utility: find best available data ----

def get_best_elevation_source():
    for p in [GEBCO_PATH, ASTER_VRT]:
        if Path(p).exists():
            return p
    return None

# ---- Static frontend ----
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    from fastapi.responses import HTMLResponse

    @app.get("/")
    async def index():
        return HTMLResponse((FRONTEND_DIR / "index.html").read_text())

    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")


# ---- Tile endpoint (simple PNG tiles from COG/VRT) ----

@app.get("/api/tiles/{z}/{x}/{y}.png")
async def tile(z: int, x: int, y: int, layer: str = Query("elevation")):
    src_path = get_best_elevation_source()
    if not src_path:
        return Response(status_code=404)

    import mercantile
    import rasterio
    import numpy as np

    TILE_SIZE = 256

    try:
        with rasterio.open(src_path) as src:
            b = mercantile.bounds(x, y, z)
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

            from PIL import Image
            img = Image.fromarray(norm, mode="L")
            if width != TILE_SIZE or height != TILE_SIZE:
                resample = Image.NEAREST if width < TILE_SIZE else Image.BILINEAR
                img = img.resize((TILE_SIZE, TILE_SIZE), resample)

            import io
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
        conn = sqlite3.connect(OSM_MBTILES)
        count = conn.execute("SELECT COUNT(*) FROM tiles").fetchone()[0]
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


# ---- Offline OSM tile endpoint (from MBTiles) ----

@app.get("/api/osm-tiles/{z}/{x}/{y}.png")
async def osm_tile(z: int, x: int, y: int):
    if not Path(OSM_MBTILES).exists():
        return empty_tile(220)

    tms_y = (1 << z) - 1 - y

    try:
        conn = sqlite3.connect(OSM_MBTILES)
        conn.execute("PRAGMA query_only = 1")
        row = conn.execute(
            "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
            (z, x, tms_y),
        ).fetchone()
        conn.close()
        if row:
            return Response(content=row[0], media_type="image/png")
        else:
            logger.warning("osm_tile not in DB: z=%d x=%d y=%d tms_y=%d", z, x, y, tms_y)
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
    if Path(MRDS_GEOJSON).exists():
        try:
            with open(MRDS_GEOJSON) as f:
                fc = json.load(f)
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
    if not Path(MRDS_GEOJSON).exists():
        raise HTTPException(404, "MRDS data not available")

    with open(MRDS_GEOJSON) as f:
        fc = json.load(f)

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
    p = VECS_DIR / f"{name}.geojson"
    async def _handler():
        if not p.exists():
            raise HTTPException(404)
        return Response(content=p.read_bytes(), media_type="application/geo+json")
    return _handler

app.get("/api/mrds-layer")(serve_geojson("mrds"))
app.get("/api/geology-layer")(serve_geojson("geology"))
app.get("/api/gw-aquifertype")(serve_geojson("gw_aquifertype"))
app.get("/api/gw-depth")(serve_geojson("gw_depthtogw"))
app.get("/api/gw-salinity")(serve_geojson("gw_salinity"))
app.get("/api/gw-tba")(serve_geojson("gw_tba"))


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
        "tile_count": tile_count(),
        "tile_total_estimated": 141171,
    }
