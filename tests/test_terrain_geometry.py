"""Regression tests for terrain-case geometry generation and onboarding units.

Run with::

    backend\\venv\\Scripts\\python.exe -m unittest discover -s tests -v

These tests need neither DualSPHysics nor any local DEM upload.  The only
fixture is the tracked Chouldari SPH terrain (``data/terrain/chouldari/sph``),
which is committed with the repository.  Everything else is synthetic.

They cover the geometry-validation failures this work addressed:

* the dam valley-fit span can extend onto natural abutment above the common
  crest, producing an inverted wall segment ("Dam segment N bottom is below
  crest"); the span-trim fixpoint must remove it,
* the breach must be centred on the wall as built (span midpoint), not on the
  asymmetric seed coordinate,
* a reservoir segment whose bed rises above the waterline must be dropped
  unless ``reservoir_clip_to_wet_reach`` is enabled (opt-in; ends the segment at
  the waterline instead — "Reservoir has at least one fluid segment"),
* an upstream sign requires an explicit flow axis, both in the onboarding API
  and in the generator, so it can never be silently ignored.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = (
    REPO_ROOT / "examples" / "main" / "HADR_TerrainChouldari" / "generate_terrain_case.py"
)
CHOULDARI_CONFIG = (
    REPO_ROOT / "examples" / "main" / "HADR_TerrainChouldari" / "terrain_case_config.json"
)
CHOULDARI_NPZ = REPO_ROOT / "data" / "terrain" / "chouldari" / "sph" / "chouldari_sph_terrain.npz"
CHOULDARI_STL = REPO_ROOT / "data" / "terrain" / "chouldari" / "sph" / "chouldari_sph_terrain.stl"
SITES_PATH = REPO_ROOT / "backend" / "app" / "api" / "sites.py"
SCENARIOS_DIR = REPO_ROOT / "scenarios"
SCRIPTS_DIR = REPO_ROOT / "scripts"

# Baseline case config shared by the synthetic cases.  Values mirror the shipped
# HADR_TerrainBhakraTest config (geometry in *simulation* units, scale 0.02).
SYNTH_BASE = {
    "case_name": "SynthTest",
    "terrain_output_subdir": "terrain",
    "definition_name": "SynthTest_Def.xml",
    "runner_definition_name": "SynthTest_runner_Def.xml",
    "summary_name": "terrain_case_summary.json",
    "particle_spacing": 0.1,
    "simulation_time": 6.0,
    "time_out": 0.05,
    "domain_margin_xy": 1.0,
    "domain_margin_z_bottom": 0.5,
    "domain_margin_z_top": 1.0,
    "dam_search_radius": 8.0,
    "dam_thickness": 0.5,
    "dam_crest_height": 0.8,
    "dam_embedment": 0.1,
    "dam_min_span": 2.0,
    "dam_max_span": 12.0,
    "dam_segment_length": 0.5,
    "dam_valley_threshold": 1.5,
    "reservoir_length": 3.0,
    "reservoir_water_depth": 0.66,
    "reservoir_freeboard": 0.05,
    "reservoir_segments": 12,
    "fluid_bed_clearance": 0.16,
    "breach_enabled": True,
    "breach_width": 1.0,
    "breach_time": 0.25,
    "breach_lift_duration": 0.3,
    "breach_lift_distance": 0.5,
    "breach_motion_steps": 50,
    "breach_apron": True,
    "force_upstream_axis": "x",
    "force_upstream_sign": -1,
}


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise unittest.SkipTest(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _make_synthetic_terrain(
    directory: Path,
    *,
    floor_slope: float = 0.0,
    wall_slope: float = 2.0,
    channel_half: float = 1.0,
    center_y: float = 10.0,
    dam_x: float = 10.0,
    dam_y: float = 11.0,
    resolution: float = 0.1,
    x_range: tuple[float, float] = (0.0, 20.0),
    y_range: tuple[float, float] = (0.0, 20.0),
) -> tuple[Path, Path]:
    """A regular-grid synthetic valley with an optional rising upstream bed.

    ``wall_slope``/``channel_half`` form a flat-floored channel centred on
    ``center_y`` with linear side walls; ``floor_slope`` raises the bed for
    ``x < dam_x`` (i.e. upstream when ``force_upstream_sign`` is -1).  The STL
    is an empty file so the generator uses the NPZ vertex fallback and the
    surface is exactly the analytic function above.
    """
    xs = np.arange(x_range[0], x_range[1] + 1e-9, resolution)
    ys = np.arange(y_range[0], y_range[1] + 1e-9, resolution)
    grid_x, grid_y = np.meshgrid(xs, ys)
    flat_x = grid_x.ravel()
    flat_y = grid_y.ravel()
    wall = np.maximum(0.0, np.abs(flat_y - center_y) - channel_half) * wall_slope
    floor = np.maximum(0.0, dam_x - flat_x) * floor_slope
    z = floor + wall

    npz = directory / "synth_sph_terrain.npz"
    np.savez(
        npz,
        x_sim=flat_x,
        y_sim=flat_y,
        z_sim=z,
        scale=0.02,
        dam_x_m=np.array([dam_x / 0.02]),
        dam_y_m=np.array([dam_y / 0.02]),
    )
    stl = directory / "synth_sph_terrain.stl"
    stl.write_bytes(b"")
    return npz, stl


def _write_config(
    directory: Path, *, npz: Path, stl: Path, base: dict, **overrides
) -> Path:
    config = dict(base)
    config["terrain_npz"] = str(npz)
    config["terrain_stl"] = str(stl)
    config.update(overrides)
    case_dir = directory / "case"
    case_dir.mkdir(parents=True, exist_ok=True)
    config_path = case_dir / "terrain_case_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path


def _run_generator(module, config_path: Path, case_dir: Path) -> dict:
    module.CASE_DIR = case_dir
    return module.generate_case(config_path)


# ---------------------------------------------------------------------------
# Onboarding units (backend/app/api/sites.py)
# ---------------------------------------------------------------------------

class OnboardingUnitsTests(unittest.TestCase):
    def setUp(self):
        try:
            self.sites = _load_module("sites_under_test", SITES_PATH)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(f"cannot import sites module: {exc}")

    def test_metres_to_sim_matches_terrain_scale(self):
        self.assertAlmostEqual(self.sites._metres_to_sim(33.0), 0.66)
        self.assertAlmostEqual(self.sites._metres_to_sim(40.0), 0.80)
        self.assertAlmostEqual(self.sites._metres_to_sim(25.0), 0.50)
        self.assertAlmostEqual(self.sites._metres_to_sim(50.0), 1.00)
        self.assertAlmostEqual(self.sites._metres_to_sim(150.0), 3.00)

    def test_prepare_request_defaults_are_metres(self):
        req = self.sites.PrepareRequest(lon=76.4361, lat=31.4111)
        # Documented physical defaults (metres).
        self.assertEqual(req.reservoir_water_depth_m, 33.0)
        self.assertEqual(req.dam_crest_height_m, 40.0)
        self.assertEqual(req.dam_thickness_m, 25.0)
        self.assertEqual(req.breach_width_m, 50.0)
        self.assertEqual(req.reservoir_length_m, 150.0)
        # Dam placement defaults to the study-area centre.
        self.assertIsNone(req.dam_lon)
        self.assertIsNone(req.dam_lat)
        # Reservoir clipping is an explicit opt-in, off by default.
        self.assertFalse(req.reservoir_clip_to_wet_reach)

    def test_default_metres_reproduce_shipped_bhakra_sim_values(self):
        req = self.sites.PrepareRequest(lon=76.4361, lat=31.4111)
        self.assertAlmostEqual(
            self.sites._metres_to_sim(req.reservoir_water_depth_m), 0.66
        )
        self.assertAlmostEqual(self.sites._metres_to_sim(req.dam_crest_height_m), 0.80)
        self.assertAlmostEqual(self.sites._metres_to_sim(req.dam_thickness_m), 0.50)
        self.assertAlmostEqual(self.sites._metres_to_sim(req.dam_embedment_m), 0.10)
        self.assertAlmostEqual(self.sites._metres_to_sim(req.reservoir_length_m), 3.00)
        self.assertAlmostEqual(self.sites._metres_to_sim(req.fluid_bed_clearance_m), 0.16)
        self.assertAlmostEqual(self.sites._metres_to_sim(req.breach_width_m), 1.00)

    def _build_case_env(self, root: Path):
        """Minimal repo tree: a case template plus a previously-uploaded site."""
        data_root = root / "data" / "terrain"
        examples = root / "examples" / "main"
        scenarios = root / "scenarios"
        scenarios.mkdir(parents=True)
        # Minimal case template (a copy of the Chouldari example is enough).
        src = examples / "HADR_TerrainChouldari"
        src.mkdir(parents=True)
        (src / "generate_terrain_case.py").write_text("# placeholder\n", encoding="utf-8")
        (src / "terrain_case_config.json").write_text("{}", encoding="utf-8")
        site = data_root / "_test_site"
        (site / "metadata").mkdir(parents=True)
        (site / "metadata" / "upload.json").write_text(
            json.dumps(
                {
                    "dem_path": "data/terrain/_test_site/input/dem.tif",
                    "site_name": "Test Site",
                }
            ),
            encoding="utf-8",
        )
        return data_root, examples, scenarios

    @contextlib.contextmanager
    def _patched_roots(self, root: Path, data_root: Path, examples: Path, scenarios: Path):
        sites = self.sites
        names = ("REPO_ROOT", "DATA_ROOT", "EXAMPLES_ROOT", "SCENARIOS_DIR")
        original = {name: getattr(sites, name) for name in names}
        sites.REPO_ROOT = root
        sites.DATA_ROOT = data_root
        sites.EXAMPLES_ROOT = examples
        sites.SCENARIOS_DIR = scenarios
        try:
            yield
        finally:
            for name, value in original.items():
                setattr(sites, name, value)

    def test_prepare_site_writes_metre_conversion_and_dam_anchor(self):
        sites = self.sites
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root, examples, scenarios = self._build_case_env(root)
            with self._patched_roots(root, data_root, examples, scenarios):
                result = sites.prepare_site(
                    "_test_site",
                    sites.PrepareRequest(
                        lon=76.0,
                        lat=31.0,
                        dam_lon=76.25,
                        dam_lat=31.25,
                        dam_axis="y",
                        dam_upstream_sign=-1,
                        reservoir_water_depth_m=33.0,
                        dam_crest_height_m=40.0,
                        breach_width_m=50.0,
                    ),
                )

            self.assertTrue(result["ok"])
            scenario = json.loads((scenarios / "_test_site.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(scenario["longitude"], 76.0)
            self.assertAlmostEqual(scenario["dam_longitude"], 76.25)
            self.assertAlmostEqual(scenario["dam_latitude"], 31.25)

            case_dir = examples / "HADR_TerrainTestSite"
            cfg = json.loads((case_dir / "terrain_case_config.json").read_text(encoding="utf-8"))
            self.assertAlmostEqual(cfg["reservoir_water_depth"], 0.66)
            self.assertAlmostEqual(cfg["dam_crest_height"], 0.80)
            self.assertAlmostEqual(cfg["breach_width"], 1.00)
            self.assertEqual(cfg["force_upstream_axis"], "y")
            self.assertEqual(cfg["force_upstream_sign"], -1)
            # Reservoir clipping stays opt-in: a default request writes False.
            self.assertFalse(cfg["reservoir_clip_to_wet_reach"])
            self.assertEqual(cfg["case_name"], "HADR_TerrainTestSite")
            self.assertEqual(cfg["definition_name"], "HADR_TerrainTestSite_Def.xml")

    def test_prepare_site_opt_in_reservoir_clip(self):
        sites = self.sites
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root, examples, scenarios = self._build_case_env(root)
            with self._patched_roots(root, data_root, examples, scenarios):
                sites.prepare_site(
                    "_test_site",
                    sites.PrepareRequest(lon=76.0, lat=31.0, reservoir_clip_to_wet_reach=True),
                )
            cfg = json.loads(
                (examples / "HADR_TerrainTestSite" / "terrain_case_config.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(cfg["reservoir_clip_to_wet_reach"])

    def test_prepare_site_rejects_sign_without_axis(self):
        """An upstream sign without an explicit axis is rejected, so it can
        never be silently ignored by the generator."""
        sites = self.sites
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root, examples, scenarios = self._build_case_env(root)
            with self._patched_roots(root, data_root, examples, scenarios):
                with self.assertRaises(sites.HTTPException) as ctx:
                    sites.prepare_site(
                        "_test_site",
                        sites.PrepareRequest(lon=76.0, lat=31.0, dam_upstream_sign=-1),
                    )
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("dam_axis", str(ctx.exception.detail))
            # The rejected request never wrote a case directory.
            self.assertFalse((examples / "HADR_TerrainTestSite").exists())

    def test_prepare_site_rejects_invalid_axis(self):
        sites = self.sites
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root, examples, scenarios = self._build_case_env(root)
            with self._patched_roots(root, data_root, examples, scenarios):
                with self.assertRaises(sites.HTTPException) as ctx:
                    sites.prepare_site(
                        "_test_site",
                        sites.PrepareRequest(lon=76.0, lat=31.0, dam_axis="z"),
                    )
            self.assertEqual(ctx.exception.status_code, 400)

    # -- dam coordinate validation ------------------------------------------

    def _prepare_ok_in_tmp(self, **request_kwargs):
        """Run prepare_site in an isolated temp repo; return (result, scenario)."""
        sites = self.sites
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root, examples, scenarios = self._build_case_env(root)
            with self._patched_roots(root, data_root, examples, scenarios):
                result = sites.prepare_site(
                    "_test_site", sites.PrepareRequest(**request_kwargs)
                )
            scenario = json.loads((scenarios / "_test_site.json").read_text(encoding="utf-8"))
        return result, scenario

    def _prepare_400_in_tmp(self, **request_kwargs):
        """Run prepare_site expecting a 400; return (exception, case_created)."""
        sites = self.sites
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_root, examples, scenarios = self._build_case_env(root)
            with self._patched_roots(root, data_root, examples, scenarios):
                with self.assertRaises(sites.HTTPException) as ctx:
                    sites.prepare_site(
                        "_test_site", sites.PrepareRequest(**request_kwargs)
                    )
                case_created = (examples / "HADR_TerrainTestSite").exists()
        return ctx.exception, case_created

    def test_prepare_site_rejects_dam_lon_without_lat(self):
        exc, case_created = self._prepare_400_in_tmp(lon=76.0, lat=31.0, dam_lon=76.25)
        self.assertEqual(exc.status_code, 400)
        self.assertIn("together", str(exc.detail))
        # Validation ran before any case configuration was created.
        self.assertFalse(case_created)

    def test_prepare_site_rejects_dam_lat_without_lon(self):
        exc, case_created = self._prepare_400_in_tmp(lon=76.0, lat=31.0, dam_lat=31.25)
        self.assertEqual(exc.status_code, 400)
        self.assertIn("together", str(exc.detail))
        self.assertFalse(case_created)

    def test_prepare_site_rejects_out_of_range_dam_longitude(self):
        exc, case_created = self._prepare_400_in_tmp(
            lon=76.0, lat=31.0, dam_lon=200.0, dam_lat=31.25
        )
        self.assertEqual(exc.status_code, 400)
        self.assertIn("dam_lon", str(exc.detail))
        self.assertIn("-180 and 180", str(exc.detail))
        self.assertFalse(case_created)

    def test_prepare_site_rejects_out_of_range_dam_latitude(self):
        exc, case_created = self._prepare_400_in_tmp(
            lon=76.0, lat=31.0, dam_lon=76.25, dam_lat=100.0
        )
        self.assertEqual(exc.status_code, 400)
        self.assertIn("dam_lat", str(exc.detail))
        self.assertIn("-90 and 90", str(exc.detail))
        self.assertFalse(case_created)

    def test_prepare_site_rejects_out_of_range_study_area(self):
        exc, case_created = self._prepare_400_in_tmp(lon=200.0, lat=31.0)
        self.assertEqual(exc.status_code, 400)
        self.assertIn("lon", str(exc.detail))
        self.assertIn("-180 and 180", str(exc.detail))
        self.assertFalse(case_created)

    def test_prepare_site_accepts_valid_dam_coordinate_pair(self):
        result, scenario = self._prepare_ok_in_tmp(
            lon=76.0, lat=31.0, dam_lon=-10.5, dam_lat=45.25
        )
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(scenario["longitude"], 76.0)
        self.assertAlmostEqual(scenario["dam_longitude"], -10.5)
        self.assertAlmostEqual(scenario["dam_latitude"], 45.25)

    def test_prepare_site_accepts_dam_coordinate_boundaries(self):
        result, scenario = self._prepare_ok_in_tmp(
            lon=0.0, lat=0.0, dam_lon=180.0, dam_lat=90.0
        )
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(scenario["dam_longitude"], 180.0)
        self.assertAlmostEqual(scenario["dam_latitude"], 90.0)

    def test_prepare_site_defaults_dam_to_study_area_when_omitted(self):
        result, scenario = self._prepare_ok_in_tmp(lon=76.0, lat=31.0)
        self.assertTrue(result["ok"])
        self.assertAlmostEqual(scenario["dam_longitude"], 76.0)
        self.assertAlmostEqual(scenario["dam_latitude"], 31.0)


# ---------------------------------------------------------------------------
# Scenario dam placement (scripts/scenario_runner/config.py)
# ---------------------------------------------------------------------------

class ScenarioDamPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SCRIPTS_DIR))
        try:
            cls.config = _load_module(
                "scenario_config_under_test", SCRIPTS_DIR / "scenario_runner" / "config.py"
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(f"cannot import scenario config: {exc}")
        try:
            from scenario_runner import steps as steps_module  # type: ignore

            cls.steps = steps_module
        except Exception:  # pragma: no cover - environment dependent
            cls.steps = None

    def test_shipped_scenarios_default_dam_to_study_area(self):
        for name in ("chouldari", "bhakra_test", "bhakra_repair_test"):
            scenario = self.config.load_scenario(name)
            self.assertAlmostEqual(scenario.dam_longitude, scenario.longitude)
            self.assertAlmostEqual(scenario.dam_latitude, scenario.latitude)

    def test_explicit_dam_placement_is_loaded(self):
        original = self.config.SCENARIOS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            data = json.loads((SCENARIOS_DIR / "chouldari.json").read_text(encoding="utf-8"))
            data["name"] = "tmp_dam_placement"
            data["dam_longitude"] = 92.0001
            data["dam_latitude"] = 11.0002
            (tmp_dir / "tmp_dam_placement.json").write_text(
                json.dumps(data), encoding="utf-8"
            )
            self.config.SCENARIOS_DIR = tmp_dir
            try:
                scenario = self.config.load_scenario("tmp_dam_placement")
            finally:
                self.config.SCENARIOS_DIR = original
            self.assertAlmostEqual(scenario.dam_longitude, 92.0001)
            self.assertAlmostEqual(scenario.dam_latitude, 11.0002)
            # Study-area centre is untouched by the dam anchor.
            self.assertNotAlmostEqual(scenario.longitude, scenario.dam_longitude)

    def test_out_of_range_dam_placement_is_rejected(self):
        original = self.config.SCENARIOS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            data = json.loads((SCENARIOS_DIR / "chouldari.json").read_text(encoding="utf-8"))
            data["name"] = "tmp_bad_dam"
            data["dam_longitude"] = 999.0
            data["dam_latitude"] = 11.0
            (tmp_dir / "tmp_bad_dam.json").write_text(json.dumps(data), encoding="utf-8")
            self.config.SCENARIOS_DIR = tmp_dir
            try:
                with self.assertRaises(self.config.ScenarioError):
                    self.config.load_scenario("tmp_bad_dam")
            finally:
                self.config.SCENARIOS_DIR = original

    def test_build_plan_anchors_geometry_on_dam_not_study_area(self):
        if self.steps is None:
            self.skipTest("scenario_runner.steps not importable")
        original = self.config.SCENARIOS_DIR
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            data = json.loads((SCENARIOS_DIR / "chouldari.json").read_text(encoding="utf-8"))
            data["name"] = "tmp_dam_anchor"
            # Explicit dam anchor distinct from the study-area centre.
            data["dam_longitude"] = 92.5
            data["dam_latitude"] = 11.5
            (tmp_dir / "tmp_dam_anchor.json").write_text(json.dumps(data), encoding="utf-8")
            self.config.SCENARIOS_DIR = tmp_dir
            try:
                scenario = self.config.load_scenario("tmp_dam_anchor")
            finally:
                self.config.SCENARIOS_DIR = original

            plan = self.steps.build_plan(
                scenario, REPO_ROOT / "simulations" / "_plan_test"
            )
            by_id = {step.id: step for step in plan}
            for step_id in ("condition", "flow", "domain", "sph_terrain"):
                self.assertIn(step_id, by_id)
                self.assertAlmostEqual(
                    by_id[step_id].params["dam_longitude"], 92.5
                )
                self.assertAlmostEqual(
                    by_id[step_id].params["dam_latitude"], 11.5
                )


# ---------------------------------------------------------------------------
# Generator geometry
# ---------------------------------------------------------------------------

class GeneratorGeometryTests(unittest.TestCase):
    def setUp(self):
        self.gen = _load_module("gen_under_test", GENERATOR_PATH)

    def test_chouldari_regression_passes_and_is_breach_centred(self):
        self.assertTrue(CHOULDARI_NPZ.exists(), f"missing fixture {CHOULDARI_NPZ}")
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "HADR_TerrainChouldari"
            case_dir.mkdir(parents=True, exist_ok=True)
            summary = _run_generator(self.gen, CHOULDARI_CONFIG, case_dir)

        validation = summary["validation"]
        self.assertEqual(validation["status"], "pass", validation.get("errors"))
        self.assertGreaterEqual(validation["checks_passed"], 78)

        span_start = summary["dam"]["span_start"]
        span_end = summary["dam"]["span_end"]
        midpoint = 0.5 * (span_start + span_end)
        self.assertAlmostEqual(summary["breach"]["center"], midpoint, places=6)
        self.assertGreaterEqual(summary["reservoir"]["fluid_segment_count"], 6)

    def test_raw_span_would_invert_an_abutment_and_is_trimmed(self):
        """The valley-fit span reaches ground above the crest; the trim removes
        the inverted end segment while keeping the crest and checks active."""
        captured: list[list] = []
        original = self.gen._make_wall_boxes

        def capture(*args, **kwargs):
            boxes = original(*args, **kwargs)
            captured.append(list(boxes))
            return boxes

        self.gen._make_wall_boxes = capture
        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                npz, stl = _make_synthetic_terrain(tmp_dir)
                config_path = _write_config(
                    tmp_dir, npz=npz, stl=stl, base=SYNTH_BASE
                )
                summary = _run_generator(self.gen, config_path, tmp_dir / "case")
        finally:
            self.gen._make_wall_boxes = original

        # The very first _build_walls() run (raw span) must contain an inverted
        # segment: bottom at/above the common crest.
        raw_boxes = [box for call in captured[:3] for box in call]
        self.assertTrue(
            any(box.z0 >= box.z1 - 1e-9 for box in raw_boxes),
            "expected the raw span to produce an inverted wall segment",
        )

        # After the trim it validates, and the breach is centred on the wall as
        # built, not on the off-centre seed coordinate.
        self.assertEqual(
            summary["validation"]["status"], "pass", summary["validation"].get("errors")
        )
        span_start = summary["dam"]["span_start"]
        span_end = summary["dam"]["span_end"]
        midpoint = 0.5 * (span_start + span_end)
        self.assertAlmostEqual(summary["breach"]["center"], midpoint, places=6)
        self.assertNotAlmostEqual(summary["breach"]["center"], 11.0, places=3)

    def test_force_sign_without_axis_is_rejected(self):
        """A forced upstream sign with an automatic axis fails loudly instead
        of being silently discarded."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            npz, stl = _make_synthetic_terrain(tmp_dir)
            config_path = _write_config(
                tmp_dir,
                npz=npz,
                stl=stl,
                base={
                    **SYNTH_BASE,
                    "force_upstream_axis": None,
                    "force_upstream_sign": -1,
                },
            )
            with self.assertRaises(ValueError):
                _run_generator(self.gen, config_path, tmp_dir / "case")

    def test_invalid_force_axis_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            npz, stl = _make_synthetic_terrain(tmp_dir)
            config_path = _write_config(
                tmp_dir,
                npz=npz,
                stl=stl,
                base={**SYNTH_BASE, "force_upstream_axis": "z"},
            )
            with self.assertRaises(ValueError):
                _run_generator(self.gen, config_path, tmp_dir / "case")

    def test_forced_axis_and_sign_are_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            npz, stl = _make_synthetic_terrain(tmp_dir)
            config_path = _write_config(
                tmp_dir,
                npz=npz,
                stl=stl,
                base={
                    **SYNTH_BASE,
                    "force_upstream_axis": "x",
                    "force_upstream_sign": -1,
                },
            )
            summary = _run_generator(self.gen, config_path, tmp_dir / "case")
        self.assertEqual(summary["upstream"]["flow_axis"], "x")
        self.assertEqual(summary["upstream"]["method"], "forced-by-config")
        self.assertLess(summary["upstream"]["upstream_sign"], 0)

    def test_reservoir_clip_recovers_submerged_reach(self):
        """Bed rising above the waterline: clip off drops the segment (and
        fails validation), clip on keeps the submerged reach."""
        with tempfile.TemporaryDirectory() as tmp_on:
            tmp_on_dir = Path(tmp_on)
            npz, stl = _make_synthetic_terrain(tmp_on_dir, floor_slope=0.3, dam_y=10.0)
            cfg_on = _write_config(
                tmp_on_dir,
                npz=npz,
                stl=stl,
                base={**SYNTH_BASE, "dam_valley_threshold": 0.3},
                reservoir_clip_to_wet_reach=True,
            )
            summary_on = _run_generator(self.gen, cfg_on, tmp_on_dir / "case")

        with tempfile.TemporaryDirectory() as tmp_off:
            tmp_off_dir = Path(tmp_off)
            npz, stl = _make_synthetic_terrain(tmp_off_dir, floor_slope=0.3, dam_y=10.0)
            cfg_off = _write_config(
                tmp_off_dir,
                npz=npz,
                stl=stl,
                base={**SYNTH_BASE, "dam_valley_threshold": 0.3},
                reservoir_clip_to_wet_reach=False,
            )
            summary_off = _run_generator(self.gen, cfg_off, tmp_off_dir / "case")

        self.assertEqual(
            summary_on["validation"]["status"], "pass", summary_on["validation"].get("errors")
        )
        self.assertGreaterEqual(summary_on["reservoir"]["fluid_segment_count"], 1)

        self.assertEqual(summary_off["validation"]["status"], "fail")
        self.assertTrue(
            any(
                "Reservoir has at least one fluid segment" in err
                for err in summary_off["validation"].get("errors", [])
            ),
            summary_off["validation"].get("errors"),
        )
        self.assertEqual(summary_off["reservoir"]["fluid_segment_count"], 0)

    def test_valid_synthetic_valley_passes_validation(self):
        """A synthetic valley with rising abutments is a valid geometry and is
        not mistaken for a flat (DSM water-plane) dam anchor."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            npz, stl = _make_synthetic_terrain(tmp_dir)
            config_path = _write_config(tmp_dir, npz=npz, stl=stl, base=SYNTH_BASE)
            summary = _run_generator(self.gen, config_path, tmp_dir / "case")

        self.assertEqual(
            summary["validation"]["status"], "pass", summary["validation"].get("errors")
        )
        diag = summary["terrain_diagnostic"]
        self.assertFalse(diag["is_flat_water_plane"], diag)
        self.assertLess(diag["flat_fraction"], 0.35)

    def test_submerged_dam_endpoints_fail_with_flat_water_diagnostic(self):
        """A broad flat plane (the signature of the water surface a DSM records
        over a reservoir) leaves both dam endpoints below the water line:
        validation fails, the failure names the flat surface and the missing
        bathymetry, and no DualSPHysics run is attempted."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # wall_slope=0 flattens the whole surface so the anchor sits on a
            # near-constant plane with no abutment above the water line.
            npz, stl = _make_synthetic_terrain(tmp_dir, wall_slope=0.0)
            config_path = _write_config(tmp_dir, npz=npz, stl=stl, base=SYNTH_BASE)
            summary = _run_generator(self.gen, config_path, tmp_dir / "case")

        validation = summary["validation"]
        self.assertEqual(validation["status"], "fail")
        joined = " ".join(validation["errors"])
        self.assertIn("Dam low end terrain reaches water surface", joined)
        self.assertIn("Dam high end terrain reaches water surface", joined)
        self.assertIn("broad flat surface", joined)
        self.assertIn("bare-earth DEM/DTM", joined)
        self.assertTrue(summary["terrain_diagnostic"]["is_flat_water_plane"])
        self.assertFalse(validation.get("dualsphysics_run_attempted", False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
