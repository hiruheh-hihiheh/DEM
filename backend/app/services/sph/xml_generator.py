from pathlib import Path

from app.services.sph.scenario import SPHScenario


# Moving gate mk value.
#
# This is intentionally far away from mk=0, which is used for the
# fixed channel/dam boundary particles.
BREACH_GATE_MK = 200

# Short, fixed gate-lift duration for the scaled prototype.
#
# This is the time taken by the gate to move from fully closed to
# fully open once breach_time is reached.
BREACH_LIFT_DURATION = 0.5

# Extra vertical clearance so the gate bottom clears the channel top.
BREACH_LIFT_CLEARANCE = 0.05

# Number of interpolation points used for the smooth gate lift.
BREACH_MOTION_STEPS = 50

# Small time epsilon used to avoid duplicate/zero-length motion entries.
MOTION_EPS = 1e-6


def _smoothstep(progress: float) -> float:
    """
    Smooth Hermite interpolation between 0 and 1.
    """
    progress = min(max(progress, 0.0), 1.0)
    return progress * progress * (3.0 - 2.0 * progress)


def _write_gate_motion(
    motion_path: Path,
    initial_x: float,
    initial_y: float,
    initial_z: float,
    breach_time: float,
    simulation_time: float,
    lift_duration: float,
    lift_distance: float,
) -> float:
    """
    Write a DualSPHysics Motion08-style movement file.

    File columns:

        time X Y Z

    For this breach gate:

        - X remains at the gate's initial X position.
        - Y remains at the gate's initial Y position.
        - Z starts at the gate's initial Z position.
        - Z increases smoothly during the breach lift.

    The gate therefore remains aligned with the breach opening and
    only moves upward in +Z.
    """

    end_time = max(float(simulation_time), 0.0)
    start_open = max(float(breach_time), 0.0)

    # Each point stores:
    #
    #   time, vertical_offset
    #
    # vertical_offset is added to initial_z when writing the file.
    points = [(0.0, 0.0)]

    if end_time <= MOTION_EPS:
        points = [(0.0, 0.0)]
        duration = MOTION_EPS

    else:
        # Hold closed until breach_time.
        if start_open > 0.0:
            hold_time = min(start_open, end_time)
            if hold_time > MOTION_EPS:
                points.append((hold_time, 0.0))

        # Smooth upward lift.
        if start_open < end_time and lift_duration > MOTION_EPS:
            lift_end = min(start_open + lift_duration, end_time)
            span = lift_end - start_open

            if span > MOTION_EPS:
                for i in range(1, BREACH_MOTION_STEPS + 1):
                    t = start_open + span * (i / BREACH_MOTION_STEPS)

                    if t > end_time:
                        t = end_time

                    progress = (t - start_open) / lift_duration
                    z_offset = lift_distance * _smoothstep(progress)

                    points.append((t, z_offset))

        # Hold open until TimeMax if the lift finished earlier.
        last_t, last_z_offset = points[-1]
        if end_time - last_t > MOTION_EPS:
            points.append((end_time, last_z_offset))

        # Remove near-duplicate time stamps.
        cleaned = []
        for t, z_offset in points:
            if cleaned and abs(t - cleaned[-1][0]) <= MOTION_EPS:
                cleaned[-1] = (cleaned[-1][0], z_offset)
            else:
                cleaned.append((t, z_offset))

        points = cleaned
        duration = max(points[-1][0], MOTION_EPS)

    lines = []

    for t, z_offset in points:
        z = initial_z + z_offset

        lines.append(
            f"{t:.8f} {initial_x:.8f} {initial_y:.8f} {z:.8f}"
        )

    motion_path.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    return duration


def generate_xml(
    scenario: SPHScenario,
    output_path: Path,
) -> Path:
    """
    Generate a DualSPHysics case-definition XML.

    The geometry is a scaled prototype derived from the
    selected dam's physical properties.
    """

    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

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

    breach_width = max(0.0, scenario.breach_width)
    breach_width = min(breach_width, channel_width)

    motion_path = output_path.with_name(
        f"{output_path.stem}_gate_motion.txt"
    )

    motion_xml = ""

    # If this remains None, the original default +50% simulation domain
    # is preserved for intact-dam runs.
    simulation_posmax_z = None

    if breach_width <= 0:
        # No breach: keep the original full solid dam wall.
        #
        # Also remove any stale gate-motion file from a previous
        # breach run so the runner does not copy it unnecessarily.
        if motion_path.exists():
            motion_path.unlink()

        dam_wall_xml = f"""                    <!-- Dam wall without breach -->

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

                    </drawbox>"""

    else:
        # Centered breach.
        #
        # Layout:
        #
        #   left fixed dam | moving gate | right fixed dam
        #
        left_width = (channel_width - breach_width) / 2.0
        right_y = left_width + breach_width

        # Initial gate position.
        #
        # This is the same point used by the gate <drawbox> below.
        gate_x = reservoir_length
        gate_y = left_width
        gate_z = 0.0

        gate_clearance = max(
            BREACH_LIFT_CLEARANCE,
            scenario.particle_spacing,
        )

        # The gate min-Z moves from gate_z to gate_z + gate_lift_distance.
        gate_lift_distance = channel_height + gate_clearance

        # Final vertical extent of the lifted gate.
        #
        # The gate box height is channel_height. After the lift, its top is:
        #
        #   gate_z_final + channel_height
        #
        gate_top_final = (
            gate_z
            + gate_lift_distance
            + channel_height
        )

        # Keep GenCase's definition domain large enough too.
        domain_height = max(
            domain_height,
            gate_top_final + gate_clearance,
        )

        # The solver's <simulationdomain> must also be large enough.
        #
        # Do not rely on "default + 50%" for moving-gate runs because
        # that default is derived from the initial particle map and can
        # be too small for the lifted gate.
        simulation_posmax_z = max(
            domain_height,
            gate_top_final + gate_clearance,
        )

        motion_duration = _write_gate_motion(
            motion_path=motion_path,
            initial_x=gate_x,
            initial_y=gate_y,
            initial_z=gate_z,
            breach_time=scenario.breach_time,
            simulation_time=scenario.simulation_time,
            lift_duration=BREACH_LIFT_DURATION,
            lift_distance=gate_lift_distance,
        )

        if left_width > 0.0:
            fixed_sections_xml = f"""                    <!-- Left dam section -->

                    <setmkbound mk="0" />

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
                            y="{left_width}"
                            z="{channel_height}" />

                    </drawbox>

                    <!-- Right dam section -->

                    <setmkbound mk="0" />

                    <drawbox>

                        <boxfill>
                            solid
                        </boxfill>

                        <point
                            x="{reservoir_length}"
                            y="{right_y}"
                            z="0" />

                        <size
                            x="{dam_width}"
                            y="{left_width}"
                            z="{channel_height}" />

                    </drawbox>

"""
        else:
            fixed_sections_xml = ""

        dam_wall_xml = f"""                    <!-- Dam wall with centered breach -->

{fixed_sections_xml}                    <!-- Moving breach gate -->

                    <setmkbound mk="{BREACH_GATE_MK}" />

                    <drawbox>

                        <boxfill>
                            solid
                        </boxfill>

                        <point
                            x="{gate_x}"
                            y="{gate_y}"
                            z="{gate_z}" />

                        <size
                            x="{dam_width}"
                            y="{breach_width}"
                            z="{channel_height}" />

                    </drawbox>"""

        motion_xml = f"""        <motion>
            <objreal ref="{BREACH_GATE_MK}">
                <begin mov="1" start="0"/>
                <mvfile id="1" duration="{motion_duration:.8f}">
                    <file name="{motion_path.name}" fields="4" fieldtime="0" fieldx="1" fieldy="2" fieldz="3"/>
                </mvfile>
            </objreal>
        </motion>"""

    if simulation_posmax_z is None:
        # Intact-dam behavior remains unchanged.
        simulation_domain_xml = """            <simulationdomain>

                <posmin
                    x="default"
                    y="default"
                    z="default" />

                <posmax
                    x="default"
                    y="default"
                    z="default + 50%" />

            </simulationdomain>"""
    else:
        # Moving-gate runs use an explicit Z maximum.
        #
        # X/Y remain default so the previous lateral domain behavior
        # is preserved.
        simulation_domain_xml = f"""            <simulationdomain>

                <posmin
                    x="default"
                    y="default"
                    z="default" />

                <posmax
                    x="default"
                    y="default"
                    z="{simulation_posmax_z:.8f}" />

            </simulationdomain>"""

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

{dam_wall_xml}

                </mainlist>

            </commands>

        </geometry>

{motion_xml}

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

{simulation_domain_xml}

        </parameters>

    </execution>

</case>
"""

    output_path.write_text(
        xml,
        encoding="utf-8",
    )

    return output_path