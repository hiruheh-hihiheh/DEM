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
    scenario: ScenarioType
    reservoir_level: float
    breach_width: float
    breach_time: float
    simulation_time: float
    particle_spacing: float


def build_scenario(
    *,
    dam_id: str,
    scenario: ScenarioType,
    reservoir_level: float,
    breach_width: float,
    breach_time: float,
    simulation_time: float,
    particle_spacing: float,
) -> SPHScenario:

    return SPHScenario(
        dam_id=dam_id,
        scenario=scenario,
        reservoir_level=reservoir_level,
        breach_width=breach_width,
        breach_time=breach_time,
        simulation_time=simulation_time,
        particle_spacing=particle_spacing,
    )