from pathlib import Path

from app.services.sph.scenario import SPHScenario


def generate_xml(
    scenario: SPHScenario,
    output_path: Path,
) -> Path:
    """
    Generate a DualSPHysics case-definition XML.

    This first version intentionally targets the small
    laboratory dam-break scenario we validated manually.
    """

    water_height = max(
        0.05,
        0.5 * scenario.reservoir_level / 100.0,
    )

    if scenario.scenario == "normal":
        water_height *= 0.6

    elif scenario.scenario == "partial":
        water_height *= 0.8

    elif scenario.scenario == "extreme":
        water_height = min(
            0.6,
            water_height * 1.2,
        )

    xml = f"""<?xml version="1.0" encoding="UTF-8" ?>
<case>

    <casedef>

        <constantsdef>

            <gravity
                x="0"
                y="0"
                z="-9.81"
                comment="Gravitational acceleration"
                units_comment="m/s^2" />

            <rhop0
                value="1000"
                comment="Reference density of the fluid"
                units_comment="kg/m^3" />

            <rhopgradient value="2" />

            <hswl
                value="0"
                auto="true" />

            <gamma value="7" />

            <speedsystem
                value="0"
                auto="true" />

            <coefsound value="20" />

            <speedsound
                value="0"
                auto="true" />

            <coefh value="1.0" />

            <_hdp value="2" />

            <cflnumber value="0.2" />

        </constantsdef>

        <mkconfig
            boundcount="240"
            fluidcount="9" />

        <geometry>

            <definition
                dp="{scenario.particle_spacing}"
                units_comment="metres (m)">

                <pointmin
                    x="-0.05"
                    y="-0.05"
                    z="-0.05" />

                <pointmax
                    x="2"
                    y="1"
                    z="1.2" />

            </definition>

            <commands>

                <mainlist>

                    <setshapemode>
                        dp | bound
                    </setshapemode>

                    <setdrawmode mode="full" />

                    <!-- Initial water -->
                    <setmkfluid mk="0" />

                    <drawbox>

                        <boxfill>
                            solid
                        </boxfill>

                        <point
                            x="0"
                            y="0"
                            z="0" />

                        <size
                            x="0.4"
                            y="0.67"
                            z="{water_height}" />

                    </drawbox>

                    <!-- Channel -->
                    <setmkbound mk="0" />

                    <drawbox>

                        <boxfill>
                            bottom | left | right | front | back
                        </boxfill>

                        <point
                            x="0"
                            y="0"
                            z="0" />

                        <size
                            x="1.6"
                            y="0.67"
                            z="0.4" />

                    </drawbox>

                </mainlist>

            </commands>

        </geometry>

    </casedef>

    <execution>

        <parameters>

            <parameter
                key="SavePosDouble"
                value="0" />

            <parameter
                key="StepAlgorithm"
                value="1" />

            <parameter
                key="VerletSteps"
                value="40" />

            <parameter
                key="Kernel"
                value="1" />

            <parameter
                key="ViscoTreatment"
                value="1" />

            <parameter
                key="Visco"
                value="0.1" />

            <parameter
                key="ViscoBoundFactor"
                value="1" />

            <parameter
                key="DensityDT"
                value="2" />

            <parameter
                key="DensityDTvalue"
                value="0.1" />

            <parameter
                key="Shifting"
                value="0" />

            <parameter
                key="RigidAlgorithm"
                value="1" />

            <parameter
                key="CoefDtMin"
                value="0.05" />

            <parameter
                key="DtIni"
                value="0" />

            <parameter
                key="DtMin"
                value="0" />

            <parameter
                key="DtFixed"
                value="0" />

            <parameter
                key="DtFixedFile"
                value="NONE" />

            <parameter
                key="DtAllParticles"
                value="0" />

            <parameter
                key="DtAllParticles"
                value="0" />

            <parameter
                key="TimeMax"
                value="{scenario.simulation_time}" />

            <parameter
                key="TimeOut"
                value="0.01" />

            <parameter
                key="PartsOutMax"
                value="1" />

            <parameter
                key="RhopOutMin"
                value="700" />

            <parameter
                key="RhopOutMax"
                value="1300" />

            <simulationdomain>

                <posmin
                    x="default"
                    y="default"
                    z="default" />

                <posmax
                    x="default"
                    y="default"
                    z="default + 50%" />

            </simulationdomain>

        </parameters>

    </execution>

</case>
"""

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_path.write_text(
        xml,
        encoding="utf-8",
    )

    return output_path