from pathlib import Path

from app.services.sph.scenario import SPHScenario


def generate_xml(
    scenario: SPHScenario,
    output_path: Path,
) -> Path:
    """
    Generate a DualSPHysics case-definition XML.

    The geometry is a scaled prototype derived from the
    selected dam's physical properties.
    """

    # Use derived geometry from SPHScenario.
    water_height = scenario.water_height
    channel_length = scenario.channel_length
    channel_width = scenario.channel_width
    channel_height = scenario.channel_height
    dam_width = scenario.dam_width

    # Keep the first reservoir section reasonably sized
    # relative to the downstream channel.
    reservoir_length = max(
        0.40,
        channel_length * 0.25,
    )

    # Leave some headroom above the initial water column.
    domain_height = max(
        channel_height + 0.20,
        water_height + 0.20,
    )

    # Keep the water safely below the top of the channel.
    water_height = min(
        water_height,
        channel_height * 0.85,
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
                    x="{channel_length + 0.10}"
                    y="{channel_width + 0.10}"
                    z="{domain_height}" />

            </definition>

            <commands>

                <mainlist>

                    <setshapemode>
                        dp | bound
                    </setshapemode>

                    <setdrawmode mode="full" />

                    <!-- Initial reservoir water -->

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
                            x="{reservoir_length}"
                            y="{channel_width}"
                            z="{water_height}" />

                    </drawbox>

                    <!-- Downstream channel boundary -->

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
                            x="{channel_length}"
                            y="{channel_width}"
                            z="{channel_height}" />

                    </drawbox>

                    <!-- Solid dam wall -->

                    <drawbox>

                        <boxfill>
                            solid
                        </boxfill>

                        <point
                            x="{reservoir_length}"
                            y="0"
                            z="0" />

                        <size
                            x="{dam_width}"
                            y="{channel_width}"
                            z="{channel_height}" />

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