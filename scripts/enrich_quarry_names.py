import json
import re
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data" / "vectors"
INPUT = DATA_DIR / "osm_mining.geojson"
OUTPUT = DATA_DIR / "osm_mining_enriched.geojson"

patterns = [
    ("sand", r'пес[окчк]|песчан|пес[кч]?[ио]?|sand|пески|супес'),
    ("gravel", r'гравий|gravel'),
    ("granite", r'гран[ии]т(?!ат)|granite'),
    ("marble", r'мрамор|marble'),
    ("limestone", r'известн|известк|известков|limestone|флюс'),
    ("coal", r'уголь|coal|буроуголь|шахт(?!а.*магнет)'),
    ("peat", r'торф|peat'),
    ("clay", r'глин[аы]|clay|огнеупорн'),
    ("gold", r'золот|gold|рудн(?!ых)|прииск|россыпн[оы]е?\s*золот'),
    ("diamond", r'алмаз|кимберлит|diamond'),
    ("iron_ore", r'желез|iron|магнетит'),
    ("copper", r'мед[ьн]|copper'),
    ("asbestos", r'асбест|asbestos'),
    ("mercury", r'ртут|mercury'),
    ("aggregate", r'щебен|aggregate|пг[мс]|пгс'),
    ("stone", r'каменоломн|stone'),
    ("dolomite", r'доломит|dolomite'),
    ("emery", r'наждак'),
    ("garnet", r'гранат(?!ь)'),
    ("chromium", r'хром|chromium'),
    ("manganese", r'марганец|manganese'),
]

with open(INPUT) as f:
    data = json.load(f)

matched = 0
already_known = 0
total_without = 0

for feat in data["features"]:
    props = feat["properties"]
    resource = props.get("resource", "")
    name = (props.get("name") or "").strip()

    if resource:
        already_known += 1
        continue

    total_without += 1

    name_lower = name.lower()
    if name_lower == "au":
        props["resource"] = "gold"
        props["enriched_by"] = "name"
        matched += 1
        continue

    for res_name, pattern in patterns:
        if re.search(pattern, name_lower):
            props["resource"] = res_name
            props["enriched_by"] = "name"
            matched += 1
            break

with open(OUTPUT, "w") as f:
    json.dump(data, f, ensure_ascii=False)

print(f"Total features: {len(data['features'])}")
print(f"Already had resource: {already_known}")
print(f"Without resource: {total_without}")
print(f"Enriched from name: {matched}")
print(f"Still unknown: {total_without - matched}")
print(f"Saved to: {OUTPUT}")
