from pathlib import Path

ROOT = Path("/mnt/hdd2/Earthdata")
DATA = ROOT / "10_server" / "data"

# Raster data paths
GEBCO_PATH = str(DATA / "gebco_2026_cog.tif")
ASTER_VRT = str(DATA / "aster" / "aster_dem.vrt")

# Vector data paths
VECTORS_DIR = str(DATA / "vectors")
VECTORS_DB = str(DATA / "vectors" / "earth_vectors.sqlite")
MRDS_GEOJSON = str(DATA / "vectors" / "mrds.geojson")

# Geology (once conversion is complete)
GEOLOGY_DB = str(DATA / "geology" / "earth_geology.sqlite")

# Radiometrics (COG rasters)
NRAD_CCM_COG = str(DATA / "radiometrics" / "NArad_CCM_cog.tif")
AUS_TERNARY_COG = str(DATA / "radiometrics" / "australia" / "radmap_cog.tif")

# MODIS land cover
MODIS_DIR = str(ROOT / "07_landcover")

# Offline OSM tiles
OSM_MBTILES = str(DATA / "tiles" / "osm_offline.mbtiles")
