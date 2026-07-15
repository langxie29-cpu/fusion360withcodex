import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / ".agents" / "skills" / "fusion-ai-modeler" / "scripts" / "validate_model_plan.py"
EXAMPLE_PATH = ROOT / ".agents" / "skills" / "fusion-ai-modeler" / "assets" / "model-plan.example.json"

SPEC = importlib.util.spec_from_file_location("validate_model_plan", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)


class ModelPlanValidationTests(unittest.TestCase):
    def test_example_is_valid(self):
        plan = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual([], VALIDATOR.validate_plan(plan))

    def test_rejects_cut_before_body(self):
        plan = {
            "version": "1.0",
            "component_name": "Invalid",
            "units": "mm",
            "features": [
                {
                    "id": "holes",
                    "type": "hole_pattern",
                    "operation": "cut",
                    "diameter": "3 mm",
                    "points": [["0 mm", "0 mm"]],
                    "depth": "through_all",
                }
            ],
        }
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("requires an earlier body" in error for error in errors))

    def test_rejects_unknown_feature_fields(self):
        plan = {
            "version": "1.0",
            "component_name": "Invalid",
            "units": "mm",
            "features": [
                {
                    "id": "base",
                    "type": "box",
                    "width": "10 mm",
                    "depth": "10 mm",
                    "height": "2 mm",
                    "unsafe_code": "exec(...)"
                }
            ],
        }
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("unknown field 'unsafe_code'" in error for error in errors))

    def test_accepts_typed_appearance_preset(self):
        plan = {
            "version": "1.0",
            "component_name": "SilverPart",
            "units": "mm",
            "features": [{
                "id": "base",
                "type": "box",
                "width": "10 mm",
                "depth": "10 mm",
                "height": "2 mm",
                "appearance": "silver_aluminum",
            }],
        }
        self.assertEqual([], VALIDATOR.validate_plan(plan))

    def test_rejects_arbitrary_appearance(self):
        plan = {
            "version": "1.0",
            "component_name": "InvalidAppearance",
            "units": "mm",
            "features": [{
                "id": "base",
                "type": "box",
                "width": "10 mm",
                "depth": "10 mm",
                "height": "2 mm",
                "appearance": "run_external_material_script",
            }],
        }
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("unsupported appearance preset" in error for error in errors))

    def test_accepts_rounded_rectangle_annulus_and_fillet(self):
        plan = {
            "version": "1.0",
            "component_name": "RefinedPart",
            "units": "mm",
            "features": [
                {
                    "id": "body",
                    "type": "sketch_extrude",
                    "operation": "new_body",
                    "profile": {
                        "shape": "rounded_rectangle",
                        "center": ["0 mm", "0 mm"],
                        "width": "20 mm",
                        "height": "40 mm",
                        "radius": "4 mm",
                    },
                    "distance": "3 mm",
                },
                {
                    "id": "roundover",
                    "type": "edge_fillet",
                    "target_body": "body",
                    "radius": "0.5 mm",
                    "selection": "top_and_bottom_perimeters",
                },
                {
                    "id": "ring",
                    "type": "sketch_extrude",
                    "operation": "new_body",
                    "profile": {
                        "shape": "annulus",
                        "center": ["0 mm", "0 mm"],
                        "outer_diameter": "10 mm",
                        "inner_diameter": "8 mm",
                    },
                    "distance": "1 mm",
                },
            ],
        }
        self.assertEqual([], VALIDATOR.validate_plan(plan))

    def test_rejects_fillet_target_that_has_not_been_created(self):
        plan = {
            "version": "1.0",
            "component_name": "InvalidFilletTarget",
            "units": "mm",
            "features": [
                {"id": "base", "type": "box", "width": "10 mm", "depth": "10 mm", "height": "2 mm"},
                {
                    "id": "roundover",
                    "type": "edge_fillet",
                    "target_body": "missing",
                    "radius": "0.5 mm",
                    "selection": "top_perimeter",
                },
            ],
        }
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("no earlier new_body named 'missing'" in error for error in errors))

    def test_rejects_duplicate_new_body_names(self):
        plan = {
            "version": "1.0",
            "component_name": "DuplicateBodies",
            "units": "mm",
            "features": [
                {"id": "one", "name": "Shared", "type": "box", "width": "10 mm", "depth": "10 mm", "height": "2 mm"},
                {"id": "two", "name": "Shared", "type": "cylinder", "diameter": "4 mm", "height": "2 mm"},
            ],
        }
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("duplicate new_body name 'Shared'" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
