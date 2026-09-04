from dataclasses import dataclass
from typing import Literal


ScenarioType = Literal[
    "normal",
    "partial",
    "full",
    "extreme",
]


@dataclass(frozen=True)
class SPHScenario:
    dam_id: str
    dam_name: str
    river: str | None
    state: str | None

    dam_height: float | None
    full_reservoir_level: float | None
    max_water_level: float | None
    gross_storage_capacity: float | None
    dead_storage_capacity: float | None
    dam_length: float | None

    scenario: ScenarioType
    reservoir_level: float
    breach_width: float
    breach_time: float
    simulation_time: float
    particle_spacing: float


def build_scenario(
    *,
    dam: dict,
    scenario: ScenarioType,
    reservoir_level: float,
    breach_width: float,
    breach_time: float,
    simulation_time: float,
    particle_spacing: float,
) -> SPHScenario:

    properties = dam.get("properties", {})

    return SPHScenario(
        dam_id=str(dam["id"]),
        dam_name=properties.get("name") or "Unknown Dam",
        river=properties.get("river"),
        state=properties.get("state"),

        dam_height=properties.get("height"),
        full_reservoir_level=properties.get(
            "full_reservoir_level"
        ),
        max_water_level=properties.get(
            "max_water_level"
        ),
        gross_storage_capacity=properties.get(
            "gross_storage_capacity"
        ),
        dead_storage_capacity=properties.get(
            "dead_storage_capacity"
        ),
        dam_length=properties.get("dam_length"),

        scenario=scenario,
        reservoir_level=reservoir_level,
        breach_width=breach_width,
        breach_time=breach_time,
        simulation_time=simulation_time,
        particle_spacing=particle_spacing,
    )