import subprocess
from pathlib import Path
from uuid import uuid4


class SPHRunner:
    def __init__(self, dualsph_root: Path):
        self.dualsph_root = dualsph_root

        self.bin_dir = (
            dualsph_root
            / "bin"
            / "windows"
        )

        self.gencase = (
            self.bin_dir
            / "GenCase_win64.exe"
        )

        self.solver = (
            self.bin_dir
            / "DualSPHysics5.4_win64.exe"
        )

        self.partvtk = (
            self.bin_dir
            / "PartVTK_win64.exe"
        )

    def validate(self) -> None:
        if not self.gencase.exists():
            raise FileNotFoundError(
                f"GenCase not found: {self.gencase}"
            )

        if not self.solver.exists():
            raise FileNotFoundError(
                f"DualSPHysics solver not found: {self.solver}"
            )

        if not self.partvtk.exists():
            raise FileNotFoundError(
                f"PartVTK not found: {self.partvtk}"
            )

    def run(
        self,
        xml_path: Path,
        runs_dir: Path,
    ) -> dict:

        self.validate()

        simulation_id = str(uuid4())[:8]

        run_dir = runs_dir / simulation_id
        run_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        case_name = "HADR_DamBreak"

        generated_xml = (
            run_dir
            / f"{case_name}_Def.xml"
        )

        generated_xml.write_text(
            xml_path.read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        output_dir = (
            run_dir
            / f"{case_name}_out"
        )

        # GenCase expects the definition file
        # without the .xml extension.
        definition_base = generated_xml.with_suffix("")

        gencase_command = [
            str(self.gencase),
            str(definition_base),
            str(output_dir / case_name),
            "-save:all",
        ]

        gencase_result = subprocess.run(
            gencase_command,
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            check=False,
        )

        if gencase_result.returncode != 0:
            gencase_log = run_dir / "gencase.log"

            gencase_log.write_text(
                gencase_result.stdout
                + "\n"
                + gencase_result.stderr,
                encoding="utf-8",
            )

            raise RuntimeError(
                "GenCase failed. "
                f"See log: {gencase_log}"
            )

        generated_case = (
            output_dir
            / case_name
        )

        # Existing working solver invocation.
        #
        # Do not pass an explicit .xml suffix here.
        # DualSPHysics expects the case prefix only.
        solver_command = [
            str(self.solver),
            "-gpu",
            str(generated_case),
            str(output_dir),
        ]

        solver_result = subprocess.run(
            solver_command,
            cwd=str(output_dir),
            capture_output=True,
            text=True,
            check=False,
        )

        solver_log = run_dir / "solver.log"

        solver_log.write_text(
            solver_result.stdout
            + "\n"
            + solver_result.stderr,
            encoding="utf-8",
        )

        if solver_result.returncode != 0:
            raise RuntimeError(
                "DualSPHysics failed. "
                f"See log: {solver_log}"
            )

        # DualSPHysics writes per-timestep particle
        # data into <case_out>/data.
        data_dir = output_dir / "data"

        if not data_dir.is_dir():
            raise RuntimeError(
                "DualSPHysics completed, but the expected "
                "particle data directory was not found. "
                f"Expected: {data_dir}"
            )

        particles_dir = output_dir / "particles"
        particles_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        # Official-style DualSPHysics 5.4 post-processing:
        #
        # PartVTK -dir <case_out>/data
        #         -file <case_name>
        #         -out <particles_dir>
        #         -save:fluid
        #
        # This converts binary particle data into a VTK
        # file series such as:
        #
        # particles/PartFluid_0000.vtk
        # particles/PartFluid_0001.vtk
        # ...
        partvtk_command = [
            str(self.partvtk),
            "-dirdata",
            str(data_dir),
            "-savevtk",
            str(particles_dir / "PartFluid"),
            "-onlytype:-all,fluid",
            "-vars:+idp,+vel,+rhop,+press,+vor,+energy",
        ]

        partvtk_result = subprocess.run(
            partvtk_command,
            cwd=str(output_dir),
            capture_output=True,
            text=True,
            check=False,
        )

        partvtk_log = run_dir / "partvtk.log"

        partvtk_log.write_text(
            partvtk_result.stdout
            + "\n"
            + partvtk_result.stderr,
            encoding="utf-8",
        )

        if partvtk_result.returncode != 0:
            raise RuntimeError(
                "PartVTK failed. "
                f"See log: {partvtk_log}"
            )

        return {
            "simulation_id": simulation_id,
            "output_directory": str(output_dir),
            "log_file": str(solver_log),
            "particles_directory": str(particles_dir),
        }