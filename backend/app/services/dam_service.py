import json
import re
from pathlib import Path


DATA_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "dams"
    / "raw"
    / "dam.geojson"
)


def parse_dms(value: str | None) -> float | None:
    """
    Convert coordinates such as:
    11° 37' 28.000" N
    92° 39' 33.000" E

    into decimal degrees.
    """
    if not value:
        return None

    value = value.strip()

    pattern = re.compile(
        r"([+-]?\d+(?:\.\d+)?)\s*°\s*"
        r"(\d+(?:\.\d+)?)?\s*['′]?\s*"
        r"(\d+(?:\.\d+)?)?\s*[\"″]?\s*"
        r"([NSEW])?",
        re.IGNORECASE,
    )

    match = pattern.search(value)

    if not match:
        return None

    degrees = float(match.group(1))
    minutes = float(match.group(2) or 0)
    seconds = float(match.group(3) or 0)
    direction = (match.group(4) or "").upper()

    decimal = degrees + (minutes / 60) + (seconds / 3600)

    if direction in {"S", "W"}:
        decimal *= -1

    return decimal


def load_dam_data() -> dict:
    if not DATA_PATH.exists():
        raise FileNotFoundError(
            f"Dam dataset not found: {DATA_PATH}"
        )

    with DATA_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_dam(feature: dict, index: int) -> dict:
    properties = feature.get("properties") or {}

    latitude = parse_dms(properties.get("latitude"))
    longitude = parse_dms(properties.get("longitude"))

    return {
        "type": "Feature",
        "id": properties.get("PIC") or str(index),
        "properties": {
            "pic": properties.get("PIC"),
            "name": properties.get("dm_name"),
            "sdso": properties.get("sdso"),
            "state": properties.get("state"),
            "district": properties.get("district"),
            "river": properties.get("river"),
            "incharge": properties.get("incharge"),
            "height": properties.get("ht_found"),
            "completion_year": properties.get("cmp_year"),
            "basin": properties.get("basin"),
            "max_water_level": properties.get("mx_wt_lel"),
            "full_reservoir_level": properties.get("frl"),
            "gross_storage_capacity": properties.get("gs_st_cap"),
            "dead_storage_capacity": properties.get("ds_sp_cap"),
            "dam_length": properties.get("dm_length"),
            "dam_type": properties.get("dm_type"),
            "purpose": properties.get("purpose"),
        },
        "geometry": {
            "type": "Point",
            "coordinates": [
                longitude,
                latitude,
            ],
        },
    }


def get_dams() -> dict:
    data = load_dam_data()

    features = [
        normalize_dam(feature, index)
        for index, feature in enumerate(
            data.get("features", [])
        )
    ]

    return {
        "type": "FeatureCollection",
        "features": features,
    }

def get_dam_by_id(dam_id: str) -> dict:
    data = get_dams()

    for feature in data["features"]:
        if str(feature["id"]) == str(dam_id):
            return feature

    raise KeyError(f"Dam not found: {dam_id}")