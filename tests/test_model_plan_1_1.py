import importlib.util
import json
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / ".agents" / "skills" / "fusion-ai-modeler" / "scripts" / "validate_model_plan.py"
MECANUM_EXAMPLE = ROOT / "examples" / "generic-mecanum-drive-module.modelplan.json"

SPEC = importlib.util.spec_from_file_location("validate_model_plan_1_1", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VALIDATOR)


def advanced_plan():
    return {
        "version": "1.1",
        "component_name": "AdvancedFixture",
        "units": "mm",
        "features": [
            {
                "id": "base",
                "name": "Base",
                "type": "box",
                "width": "80 mm",
                "depth": "50 mm",
                "height": "8 mm",
            },
            {
                "id": "side_boss",
                "name": "Side_Boss",
                "type": "cylinder",
                "axis": "x",
                "center": ["0 mm", "25 mm", "20 mm"],
                "diameter": "18 mm",
                "height": "12 mm",
            },
            {
                "id": "service_slot",
                "name": "Service_Slot_Seed",
                "type": "sketch_extrude",
                "operation": "cut",
                "plane": "xz",
                "offset": "0 mm",
                "profile": {
                    "shape": "slot",
                    "center": ["20 mm", "4 mm"],
                    "length": "18 mm",
                    "width": "5 mm",
                    "angle": "15 deg",
                },
                "distance": "50 mm",
                "direction": "positive",
            },
            {
                "id": "counterbore_seed",
                "name": "Counterbore_Seed",
                "type": "hole_pattern",
                "operation": "cut",
                "style": "counterbore",
                "plane": "xy",
                "offset": "0 mm",
                "diameter": "4.5 mm",
                "counterbore_diameter": "8 mm",
                "counterbore_depth": "3 mm",
                "points": [["15 mm", "15 mm"]],
                "depth": "through_all",
                "direction": "positive",
            },
            {
                "id": "slot_array",
                "type": "rectangular_pattern",
                "target_feature": "Service_Slot_Seed",
                "direction_one": "z",
                "quantity_one": 3,
                "spacing_one": "12 mm",
                "direction_two": "x",
                "quantity_two": 2,
                "spacing_two": "30 mm",
            },
            {
                "id": "hole_array",
                "type": "circular_pattern",
                "target_feature": "Counterbore_Seed",
                "axis": "z",
                "quantity": 4,
                "total_angle": "360 deg",
                "symmetric": False,
            },
            {
                "id": "edge_break",
                "type": "edge_chamfer",
                "target_body": "Base",
                "distance": "0.6 mm",
                "selection": "top_perimeter",
            },
        ],
        "result_checks": {
            "body_count": 2,
            "required_bodies": ["Base", "Side_Boss"],
            "minimum_size": ["80 mm", "50 mm", "20 mm"],
            "maximum_size": ["100 mm", "70 mm", "40 mm"],
        },
    }


class ModelPlan11ValidationTests(unittest.TestCase):
    def test_accepts_advanced_typed_features(self):
        self.assertEqual([], VALIDATOR.validate_plan(advanced_plan()))

    def test_accepts_brand_neutral_mecanum_example(self):
        plan = json.loads(MECANUM_EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual([], VALIDATOR.validate_plan(plan))

    def test_advanced_feature_requires_version_1_1(self):
        plan = advanced_plan()
        plan["version"] = "1.0"
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("requires ModelPlan 1.1" in error for error in errors))

    def test_rejects_partial_second_rectangular_direction(self):
        plan = advanced_plan()
        pattern = next(item for item in plan["features"] if item["type"] == "rectangular_pattern")
        del pattern["spacing_two"]
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("spacing_two" in error and "second direction" in error for error in errors))

    def test_rejects_invalid_mecanum_roller_count(self):
        plan = json.loads(MECANUM_EXAMPLE.read_text(encoding="utf-8"))
        plan = deepcopy(plan)
        plan["features"][0]["wheel"]["roller_count"] = 2
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("roller_count" in error and "3 to 32" in error for error in errors))

    def test_rejects_unknown_result_body_field(self):
        plan = advanced_plan()
        plan["result_checks"]["run_code"] = "unsafe"
        errors = VALIDATOR.validate_plan(plan)
        self.assertTrue(any("$.result_checks: unknown field 'run_code'" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
