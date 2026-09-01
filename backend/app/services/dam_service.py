import json
from pathlib import Path


DATA_PATH = (
    Path(__file__).resolve().parents[3]
    / "data"
    / "dams"
    / "raw"
    / "dam.geojson"
)


def load_dam_data() -> dict:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Dam dataset not found: {DATA_PATH}")

    with DATA_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


def normalize_dam(feature: dict, index: int) -> dict:
    properties = feature.get("properties") or {}

    return {
        "type": "Feature",
        "id": properties.get("PIC") or str(index),
        "properties": {
            "pic": properties.get("PIC"),
            "name": properties.get("dm_name"),
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
        "geometry": feature.get("geometry"),
    }


def get_dams() -> dict:
    data = load_dam_data()

    features = [
        normalize_dam(feature, index)
        for index, feature in enumerate(data.get("features", []))
    ]

    return {
        "type": "FeatureCollection",
        "features": features,
    }