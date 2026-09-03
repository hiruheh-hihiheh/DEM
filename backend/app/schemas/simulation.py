from typing import Literal

from pydantic import BaseModel, Field


ScenarioType = Literal[
    "normal",
    "partial",
    "full",
    "extreme",
]


class SPHSimulationRequest(BaseModel):
    dam_id: str = Field(..., min_length=1)

    scenario: ScenarioType = "full"

    reservoir_level: float = Field(
        ...,
        ge=0,
        le=100,
        description="Initial reservoir level as percentage of reference level.",
    )

    breach_width: float = Field(
        default=0.0,
        ge=0,
        description="Breach width in metres.",
    )

    breach_time: float = Field(
        default=0.0,
        ge=0,
        description="Breach formation time in seconds.",
    )

    simulation_time: float = Field(
        default=1.6,
        gt=0,
        le=3600,
        description="Simulation duration in seconds.",
    )

    particle_spacing: float = Field(
        default=0.0085,
        gt=0,
        description="SPH particle spacing in metres.",
    )


class SPHSimulationResponse(BaseModel):
    simulation_id: str
    status: Literal[
        "queued",
        "running",
        "completed",
        "failed",
    ]

    dam_id: str
    scenario: ScenarioType

    output_directory: str | None = None

    message: str