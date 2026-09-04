# backend/app/services/sph/scenario.py

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
    """
    SPH simulation scenario.

    breach_time is reserved for the future time-dependent breach implementation; the current breach geometry is instantaneous at simulation start.
    """

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

    # breach_time is reserved for the future time-dependent breach implementation; the current breach geometry is instantaneous at simulation start.
    breach_time: float

    simulation_time: float
    particle_spacing: float

    # Derived simulation geometry
    water_height: float
    dam_width: float
    channel_length: float
    channel_width: float
    channel_height: float


def _validate_breach_timing(
    breach_width: float,
    breach_time: float,
) -> tuple[float, float]:
    """
    Validate breach timing semantics.

    breach_time is reserved for the future time-dependent breach implementation; the current breach geometry is instantaneous at simulation start.
    """

    try:
        breach_width_value = float(breach_width)
        breach_time_value = float(breach_time)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "breach_width and breach_time must be numeric"
        ) from exc

    if not breach_time_value >= 0.0:
        raise ValueError("breach_time must be >= 0.")

    if breach_width_value == 0.0 and breach_time_value != 0.0:
        raise ValueError(
            "breach_time must be 0 when breach_width is 0."
        )

    return breach_width_value, breach_time_value


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

    breach_width, breach_time = _validate_breach_timing(
        breach_width,
        breach_time,
    )

    properties = dam.get("properties", {})

    dam_height = properties.get("height")
    full_reservoir_level = properties.get(
        "full_reservoir_level"
    )

    # Use the actual FRL as the physical reference when
    # available. Fall back to dam height only when FRL
    # is unavailable.
    reference_level = (
        full_reservoir_level
        if full_reservoir_level is not None
        else dam_height
    )

    if reference_level is None:
        reference_level = 1.0

    # Convert requested percentage into a physical
    # water elevation relative to the chosen reference.
    water_height = (
        reference_level * reservoir_level / 100.0
    )

    # Keep the first prototype within a manageable
    # numerical scale.
    scale = 0.02

    scaled_water_height = max(
        0.05,
        water_height * scale,
    )

    scaled_dam_height = max(
        0.10,
        (dam_height or reference_level) * scale,
    )

    scaled_dam_width = max(
        0.10,
        scaled_dam_height * 0.30,
    )

    scaled_channel_length = max(
        1.60,
        scaled_dam_height * 8.0,
    )

    scaled_channel_width = max(
        0.67,
        scaled_dam_width * 4.0,
    )

    scaled_channel_height = max(
        0.40,
        scaled_dam_height * 1.5,
    )

    return SPHScenario(
        dam_id=str(dam["id"]),
        dam_name=properties.get("name") or "Unknown Dam",
        river=properties.get("river"),
        state=properties.get("state"),

        dam_height=dam_height,
        full_reservoir_level=full_reservoir_level,
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

        water_height=scaled_water_height,
        dam_width=scaled_dam_width,
        channel_length=scaled_channel_length,
        channel_width=scaled_channel_width,
        channel_height=scaled_channel_height,
    )