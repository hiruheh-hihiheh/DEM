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


# ---------------------------------------------------------------------------
# Job API (scenario-runner backed, async execution)
# ---------------------------------------------------------------------------


class JobParameters(BaseModel):
    """User-facing scenario parameters.

    ``None`` means "keep the scenario's (validated) value". Reservoir level
    is a percentage of the scenario's validated fill depth (100% = the
    validated configuration).
    """

    reservoir_level: float = Field(
        default=100.0,
        ge=1.0,
        le=100.0,
        description="Initial reservoir level, percent of validated fill depth.",
    )

    breach_width: float | None = Field(
        default=None,
        ge=0.05,
        le=1000.0,
        description="Breach width in metres (model units).",
    )

    breach_time: float | None = Field(
        default=None,
        ge=0.0,
        le=86400.0,
        description="Breach formation time in seconds.",
    )

    simulation_time: float | None = Field(
        default=None,
        gt=0.0,
        le=3600.0,
        description="Simulation duration in seconds.",
    )

    particle_spacing: float | None = Field(
        default=None,
        gt=0.0,
        le=10.0,
        description="SPH particle spacing in metres (model units).",
    )

    scenario_type: ScenarioType = "full"


class CreateJobRequest(BaseModel):
    scenario: str = Field(
        default="chouldari",
        min_length=1,
        description="Scenario name from scenarios/<name>.json.",
    )

    parameters: JobParameters = Field(default_factory=JobParameters)


class CreateJobResponse(BaseModel):
    job_id: str
    status: str
    scenario: str
    validated_config: bool
    message: str