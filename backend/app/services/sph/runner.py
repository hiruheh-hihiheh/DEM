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

    def validate(self) -> None:
        if not self.gencase.exists():
            raise FileNotFoundError(
                f"GenCase not found: {self.gencase}"
            )

        if not self.solver.exists():
            raise FileNotFoundError(
                f"DualSPHysics solver not found: {self.solver}"
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

        output_base = (
            run_dir
            / f"{case_name}_out"
        )

        gencase_command = [
            str(self.gencase),
            str(generated_xml),
            str(output_base),
            "-save:all",
        ]

        gencase_result = subprocess.run(
            gencase_command,
            cwd=str(
                xml_path.parent
            ),
            capture_output=True,
            text=True,
            check=False,
        )

        if gencase_result.returncode != 0:
            raise RuntimeError(
                "GenCase failed.\n"
                + gencase_result.stdout
                + "\n"
                + gencase_result.stderr
            )

        solver_command = [
            str(self.solver),
            "-gpu",
            str(
                output_base
                / f"{case_name}.xml"
            ),
            str(output_base),
        ]

        solver_result = subprocess.run(
            solver_command,
            cwd=str(
                xml_path.parent
            ),
            capture_output=True,
            text=True,
            check=False,
        )

        log_path = run_dir / "solver.log"

        log_path.write_text(
            solver_result.stdout
            + "\n"
            + solver_result.stderr,
            encoding="utf-8",
        )

        if solver_result.returncode != 0:
            raise RuntimeError(
                "DualSPHysics failed. "
                f"See log: {log_path}"
            )

        return {
            "simulation_id": simulation_id,
            "output_directory": str(
                output_base
            ),
            "log_file": str(log_path),
        }