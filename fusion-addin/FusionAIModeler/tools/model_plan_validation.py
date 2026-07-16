#!/usr/bin/env python3
"""Validate Fusion AI ModelPlan JSON without third-party dependencies."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
OPERATIONS = {"new_body", "join", "cut", "intersect"}
APPEARANCE_PRESETS = {"silver_aluminum", "silver_glass", "silver_metal", "black_glass", "black", "flash_white"}
PLANES = {"xy", "xz", "yz"}
AXES = {"x", "y", "z"}
EDGE_SELECTIONS = {"top_perimeter", "bottom_perimeter", "top_and_bottom_perimeters", "all"}
ADVANCED_FEATURES = {"mecanum_drive", "edge_chamfer", "rectangular_pattern", "circular_pattern"}
MODIFIER_FEATURES = {"edge_fillet", "edge_chamfer", "rectangular_pattern", "circular_pattern"}
TOP_KEYS = {"version", "component_name", "units", "parameters", "features", "assumptions", "source_analysis", "result_checks"}
FEATURE_KEYS = {
    "box": {"id", "type", "name", "operation", "appearance", "origin", "width", "depth", "height"},
    "cylinder": {"id", "type", "name", "operation", "appearance", "center", "diameter", "height", "axis"},
    "sketch_extrude": {"id", "type", "name", "operation", "appearance", "plane", "offset", "profile", "distance", "direction"},
    "hole_pattern": {
        "id", "type", "name", "operation", "appearance", "plane", "offset", "diameter", "points", "depth", "direction",
        "style", "counterbore_diameter", "counterbore_depth", "countersink_diameter", "countersink_angle",
    },
    "mecanum_drive": {"id", "type", "name", "center", "side", "handedness", "wheel", "coupler", "motor", "bracket", "hardware", "appearances"},
    "edge_fillet": {"id", "type", "name", "target_body", "radius", "selection"},
    "edge_chamfer": {"id", "type", "name", "target_body", "distance", "selection"},
    "rectangular_pattern": {
        "id", "type", "name", "target_feature", "direction_one", "quantity_one", "spacing_one",
        "direction_two", "quantity_two", "spacing_two",
    },
    "circular_pattern": {"id", "type", "name", "target_feature", "axis", "quantity", "total_angle", "symmetric"},
}

MECANUM_SECTION_KEYS = {
    "wheel": {
        "diameter", "width", "roller_count", "roller_diameter", "roller_end_diameter",
        "roller_length", "roller_angle", "roller_axle_diameter", "roller_axle_overhang",
        "hub_diameter", "hub_width", "flange_diameter", "flange_thickness",
        "hex_socket_depth", "spoke_inner_diameter", "spoke_width", "spoke_thickness",
    },
    "coupler": {
        "hex_af", "hex_length", "barrel_diameter", "barrel_length", "bore_diameter",
        "set_screw_diameter", "retention_thread_diameter", "shaft_engagement",
    },
    "motor": {
        "shaft_diameter", "shaft_flat", "shaft_length", "boss_diameter", "boss_length",
        "gearbox_diameter", "gearbox_length", "can_diameter", "can_length",
        "encoder_diameter", "encoder_length", "mount_pcd", "mount_hole_count",
        "mount_thread_diameter", "mount_thread_depth",
    },
    "bracket": {
        "plate_thickness", "base_length", "width", "height", "axis_height",
        "boss_clearance", "keyhole_head_diameter", "keyhole_slot_width",
        "keyhole_slot_length", "base_hole_diameter", "base_hole_near",
        "base_hole_far", "base_hole_pitch_y",
    },
    "hardware": {
        "motor_bolt_count", "motor_bolt_diameter", "motor_bolt_head_diameter",
        "motor_bolt_head_thickness", "base_bolt_diameter", "base_bolt_length",
        "base_bolt_head_diameter", "base_bolt_head_thickness", "wheel_screw_diameter",
        "wheel_screw_length", "wheel_screw_head_diameter", "wheel_screw_head_thickness",
    },
    "appearances": {"hub", "roller", "axle", "coupler", "motor", "encoder", "bracket", "hardware"},
}


def _unknown_keys(value: dict[str, Any], allowed: set[str], path: str, errors: list[str]) -> None:
    for key in sorted(set(value) - allowed):
        errors.append(f"{path}: unknown field '{key}'")


def _expression(value: Any, path: str, errors: list[str], *, through_all: bool = False) -> None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{path}: expected a non-empty unit-bearing expression string")
    elif len(value) > 128:
        errors.append(f"{path}: expression is longer than 128 characters")
    elif value == "through_all" and not through_all:
        errors.append(f"{path}: 'through_all' is only valid for hole_pattern.depth")


def _point(value: Any, size: int, path: str, errors: list[str]) -> None:
    if not isinstance(value, list) or len(value) != size:
        errors.append(f"{path}: expected an array of {size} expressions")
        return
    for index, coordinate in enumerate(value):
        _expression(coordinate, f"{path}[{index}]", errors)


def _profile(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{path}: expected an object")
        return
    shape = value.get("shape")
    if shape == "rectangle":
        allowed = {"shape", "origin", "width", "height"}
        required = allowed
        _unknown_keys(value, allowed, path, errors)
        for key in sorted(required - set(value)):
            errors.append(f"{path}: missing required field '{key}'")
        if "origin" in value:
            _point(value["origin"], 2, f"{path}.origin", errors)
        for key in ("width", "height"):
            if key in value:
                _expression(value[key], f"{path}.{key}", errors)
    elif shape == "circle":
        allowed = {"shape", "center", "diameter"}
        _unknown_keys(value, allowed, path, errors)
        for key in sorted(allowed - set(value)):
            errors.append(f"{path}: missing required field '{key}'")
        if "center" in value:
            _point(value["center"], 2, f"{path}.center", errors)
        if "diameter" in value:
            _expression(value["diameter"], f"{path}.diameter", errors)
    elif shape == "polyline":
        allowed = {"shape", "points"}
        _unknown_keys(value, allowed, path, errors)
        points = value.get("points")
        if not isinstance(points, list) or not 3 <= len(points) <= 256:
            errors.append(f"{path}.points: expected 3 to 256 points")
        else:
            for index, point in enumerate(points):
                _point(point, 2, f"{path}.points[{index}]", errors)
    elif shape == "rounded_rectangle":
        allowed = {"shape", "center", "width", "height", "radius"}
        _unknown_keys(value, allowed, path, errors)
        for key in sorted(allowed - set(value)):
            errors.append(f"{path}: missing required field '{key}'")
        if "center" in value:
            _point(value["center"], 2, f"{path}.center", errors)
        for key in ("width", "height", "radius"):
            if key in value:
                _expression(value[key], f"{path}.{key}", errors)
    elif shape == "annulus":
        allowed = {"shape", "center", "outer_diameter", "inner_diameter"}
        _unknown_keys(value, allowed, path, errors)
        for key in sorted(allowed - set(value)):
            errors.append(f"{path}: missing required field '{key}'")
        if "center" in value:
            _point(value["center"], 2, f"{path}.center", errors)
        for key in ("outer_diameter", "inner_diameter"):
            if key in value:
                _expression(value[key], f"{path}.{key}", errors)
    elif shape == "slot":
        allowed = {"shape", "center", "length", "width", "angle"}
        required = {"shape", "center", "length", "width"}
        _unknown_keys(value, allowed, path, errors)
        for key in sorted(required - set(value)):
            errors.append(f"{path}: missing required field '{key}'")
        if "center" in value:
            _point(value["center"], 2, f"{path}.center", errors)
        for key in ("length", "width", "angle"):
            if key in value:
                _expression(value[key], f"{path}.{key}", errors)
    else:
        errors.append(f"{path}.shape: expected rectangle, circle, polyline, rounded_rectangle, annulus, or slot")


def validate_plan(plan: Any) -> list[str]:
    """Return a stable list of validation errors; an empty list means valid."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["$: expected a JSON object"]

    _unknown_keys(plan, TOP_KEYS, "$", errors)
    for key in ("version", "component_name", "units", "features"):
        if key not in plan:
            errors.append(f"$: missing required field '{key}'")

    version = plan.get("version")
    if version not in {"1.0", "1.1"}:
        errors.append("$.version: expected '1.0' or '1.1'")
    name = plan.get("component_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 80:
        errors.append("$.component_name: expected 1 to 80 characters")
    if plan.get("units") not in {"mm", "cm", "in"}:
        errors.append("$.units: expected mm, cm, or in")

    parameters = plan.get("parameters", [])
    if not isinstance(parameters, list) or len(parameters) > 128:
        errors.append("$.parameters: expected an array with at most 128 entries")
        parameters = []
    parameter_names: set[str] = set()
    for index, parameter in enumerate(parameters):
        path = f"$.parameters[{index}]"
        if not isinstance(parameter, dict):
            errors.append(f"{path}: expected an object")
            continue
        allowed = {"name", "expression", "unit", "comment"}
        _unknown_keys(parameter, allowed, path, errors)
        for key in ("name", "expression", "unit"):
            if key not in parameter:
                errors.append(f"{path}: missing required field '{key}'")
        parameter_name = parameter.get("name")
        if not isinstance(parameter_name, str) or not NAME_RE.fullmatch(parameter_name):
            errors.append(f"{path}.name: expected an ASCII Fusion parameter name")
        elif parameter_name in parameter_names:
            errors.append(f"{path}.name: duplicate parameter '{parameter_name}'")
        else:
            parameter_names.add(parameter_name)
        if "expression" in parameter:
            _expression(parameter["expression"], f"{path}.expression", errors)
        if parameter.get("unit") not in {"mm", "cm", "in", "deg"}:
            errors.append(f"{path}.unit: expected mm, cm, in, or deg")
        if "comment" in parameter and (not isinstance(parameter["comment"], str) or len(parameter["comment"]) > 300):
            errors.append(f"{path}.comment: expected a string up to 300 characters")

    features = plan.get("features")
    if not isinstance(features, list) or not 1 <= len(features) <= 128:
        errors.append("$.features: expected an array with 1 to 128 entries")
        features = []
    feature_ids: set[str] = set()
    feature_names: set[str] = set()
    body_exists = False
    body_names: set[str] = set()
    for index, feature in enumerate(features):
        path = f"$.features[{index}]"
        if not isinstance(feature, dict):
            errors.append(f"{path}: expected an object")
            continue
        feature_type = feature.get("type")
        allowed = FEATURE_KEYS.get(feature_type)
        if allowed is None:
            errors.append(f"{path}.type: unsupported feature type '{feature_type}'")
            continue
        if version == "1.0" and feature_type in ADVANCED_FEATURES:
            errors.append(f"{path}.type: '{feature_type}' requires ModelPlan 1.1")
        _unknown_keys(feature, allowed, path, errors)
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or len(feature_id) > 64 or not ID_RE.fullmatch(feature_id):
            errors.append(f"{path}.id: expected an ASCII feature identifier")
        elif feature_id in feature_ids:
            errors.append(f"{path}.id: duplicate feature id '{feature_id}'")
        else:
            feature_ids.add(feature_id)

        feature_name = feature.get("name", feature_id)
        if "name" in feature and (not isinstance(feature["name"], str) or not feature["name"].strip() or len(feature["name"]) > 100):
            errors.append(f"{path}.name: expected 1 to 100 characters")
            feature_name = None
        if version == "1.1" and isinstance(feature_name, str) and feature_name:
            if feature_name in feature_names:
                errors.append(f"{path}.name: duplicate effective feature name '{feature_name}'")

        if feature_type in {"edge_fillet", "edge_chamfer"}:
            operation = None
            if not body_exists:
                errors.append(f"{path}: {feature_type} requires an earlier body")
            target_body = feature.get("target_body")
            if isinstance(target_body, str) and target_body.strip() and target_body not in body_names:
                errors.append(f"{path}.target_body: no earlier new_body named '{target_body}'")
        elif feature_type in {"rectangular_pattern", "circular_pattern"}:
            operation = None
            target_feature = feature.get("target_feature")
            if isinstance(target_feature, str) and target_feature.strip() and target_feature not in feature_names:
                errors.append(f"{path}.target_feature: no earlier feature named '{target_feature}'")
        else:
            default_operation = "cut" if feature_type == "hole_pattern" else "new_body"
            operation = feature.get("operation", default_operation)
            if operation not in OPERATIONS:
                errors.append(f"{path}.operation: unsupported operation '{operation}'")
            if feature_type == "hole_pattern" and operation != "cut":
                errors.append(f"{path}.operation: hole_pattern must use cut")
        if "appearance" in feature and feature["appearance"] not in APPEARANCE_PRESETS:
            errors.append(f"{path}.appearance: unsupported appearance preset '{feature['appearance']}'")
        if feature_type not in MODIFIER_FEATURES and operation != "new_body" and not body_exists:
            errors.append(f"{path}.operation: '{operation}' requires an earlier body")
        if operation == "new_body":
            body_exists = True
            body_name = feature.get("name", feature_id)
            if feature_type != "mecanum_drive" and isinstance(body_name, str) and body_name:
                if body_name in body_names:
                    errors.append(f"{path}.name: duplicate new_body name '{body_name}'")
                else:
                    body_names.add(body_name)

        if feature_type == "box":
            for key in ("width", "depth", "height"):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
                else:
                    _expression(feature[key], f"{path}.{key}", errors)
            if "origin" in feature:
                _point(feature["origin"], 3, f"{path}.origin", errors)
        elif feature_type == "cylinder":
            for key in ("diameter", "height"):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
                else:
                    _expression(feature[key], f"{path}.{key}", errors)
            if "center" in feature:
                _point(feature["center"], 3, f"{path}.center", errors)
            if feature.get("axis", "z") not in AXES:
                errors.append(f"{path}.axis: expected x, y, or z")
            if version == "1.0" and "axis" in feature:
                errors.append(f"{path}.axis: requires ModelPlan 1.1")
        elif feature_type == "sketch_extrude":
            plane = feature.get("plane", "xy")
            if plane not in PLANES:
                errors.append(f"{path}.plane: expected xy, xz, or yz")
            elif version == "1.0" and plane != "xy":
                errors.append(f"{path}.plane: non-xy planes require ModelPlan 1.1")
            if "profile" not in feature:
                errors.append(f"{path}: missing required field 'profile'")
            else:
                _profile(feature["profile"], f"{path}.profile", errors)
                if version == "1.0" and isinstance(feature["profile"], dict) and feature["profile"].get("shape") == "slot":
                    errors.append(f"{path}.profile.shape: slot requires ModelPlan 1.1")
            if "distance" not in feature:
                errors.append(f"{path}: missing required field 'distance'")
            else:
                _expression(feature["distance"], f"{path}.distance", errors)
            if "offset" in feature:
                _expression(feature["offset"], f"{path}.offset", errors)
            if feature.get("direction", "positive") not in {"positive", "negative"}:
                errors.append(f"{path}.direction: expected positive or negative")
        elif feature_type == "hole_pattern":
            plane = feature.get("plane", "xy")
            if plane not in PLANES:
                errors.append(f"{path}.plane: expected xy, xz, or yz")
            elif version == "1.0" and plane != "xy":
                errors.append(f"{path}.plane: non-xy planes require ModelPlan 1.1")
            for key in ("diameter", "depth"):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
                else:
                    _expression(feature[key], f"{path}.{key}", errors, through_all=(key == "depth"))
            points = feature.get("points")
            if not isinstance(points, list) or not 1 <= len(points) <= 256:
                errors.append(f"{path}.points: expected 1 to 256 points")
            else:
                for point_index, point in enumerate(points):
                    _point(point, 2, f"{path}.points[{point_index}]", errors)
            if "offset" in feature:
                _expression(feature["offset"], f"{path}.offset", errors)
            if feature.get("direction", "positive") not in {"positive", "negative"}:
                errors.append(f"{path}.direction: expected positive or negative")
            style = feature.get("style", "simple")
            if style not in {"simple", "counterbore", "countersink"}:
                errors.append(f"{path}.style: expected simple, counterbore, or countersink")
            advanced_hole_keys = {
                "style", "counterbore_diameter", "counterbore_depth", "countersink_diameter", "countersink_angle"
            }
            if version == "1.0" and advanced_hole_keys & set(feature):
                errors.append(f"{path}: compound-hole fields require ModelPlan 1.1")
            counterbore_keys = {"counterbore_diameter", "counterbore_depth"}
            countersink_keys = {"countersink_diameter", "countersink_angle"}
            if style == "simple":
                for key in sorted((counterbore_keys | countersink_keys) & set(feature)):
                    errors.append(f"{path}.{key}: not valid for simple hole style")
            elif style == "counterbore":
                for key in sorted(counterbore_keys - set(feature)):
                    errors.append(f"{path}: missing required field '{key}' for counterbore")
                for key in sorted(countersink_keys & set(feature)):
                    errors.append(f"{path}.{key}: not valid for counterbore hole style")
            elif style == "countersink":
                for key in sorted(countersink_keys - set(feature)):
                    errors.append(f"{path}: missing required field '{key}' for countersink")
                for key in sorted(counterbore_keys & set(feature)):
                    errors.append(f"{path}.{key}: not valid for countersink hole style")
            for key in sorted((counterbore_keys | countersink_keys) & set(feature)):
                _expression(feature[key], f"{path}.{key}", errors)
        elif feature_type == "mecanum_drive":
            for key in ("center", "side", "handedness", *MECANUM_SECTION_KEYS):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
            if "center" in feature:
                _point(feature["center"], 3, f"{path}.center", errors)
            if feature.get("side") not in {"left", "right"}:
                errors.append(f"{path}.side: expected left or right")
            if feature.get("handedness") not in {"A", "B"}:
                errors.append(f"{path}.handedness: expected A or B")
            for section_name, allowed_keys in MECANUM_SECTION_KEYS.items():
                if section_name not in feature:
                    continue
                section = feature[section_name]
                section_path = f"{path}.{section_name}"
                if not isinstance(section, dict):
                    errors.append(f"{section_path}: expected an object")
                    continue
                _unknown_keys(section, allowed_keys, section_path, errors)
                for key in sorted(allowed_keys - set(section)):
                    errors.append(f"{section_path}: missing required field '{key}'")
                for key in sorted(allowed_keys & set(section)):
                    value = section[key]
                    value_path = f"{section_path}.{key}"
                    if section_name == "appearances":
                        if value not in APPEARANCE_PRESETS:
                            errors.append(f"{value_path}: unsupported appearance preset '{value}'")
                    elif key == "roller_count":
                        if type(value) is not int or not 3 <= value <= 32:
                            errors.append(f"{value_path}: expected an integer from 3 to 32")
                    elif key == "mount_hole_count":
                        if type(value) is not int or not 3 <= value <= 12:
                            errors.append(f"{value_path}: expected an integer from 3 to 12")
                    elif key == "motor_bolt_count":
                        if type(value) is not int or not 1 <= value <= 12:
                            errors.append(f"{value_path}: expected an integer from 1 to 12")
                    else:
                        _expression(value, value_path, errors)
        elif feature_type in {"edge_fillet", "edge_chamfer"}:
            size_key = "radius" if feature_type == "edge_fillet" else "distance"
            for key in ("target_body", size_key, "selection"):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
            if "target_body" in feature and (not isinstance(feature["target_body"], str) or not feature["target_body"].strip()):
                errors.append(f"{path}.target_body: expected a non-empty body name")
            if size_key in feature:
                _expression(feature[size_key], f"{path}.{size_key}", errors)
            if feature.get("selection") not in EDGE_SELECTIONS:
                errors.append(f"{path}.selection: unsupported edge selection")
        elif feature_type == "rectangular_pattern":
            for key in ("target_feature", "direction_one", "quantity_one", "spacing_one"):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
            if "target_feature" in feature and (not isinstance(feature["target_feature"], str) or not feature["target_feature"].strip()):
                errors.append(f"{path}.target_feature: expected a non-empty feature name")
            if feature.get("direction_one") not in AXES:
                errors.append(f"{path}.direction_one: expected x, y, or z")
            if "quantity_one" in feature and (type(feature["quantity_one"]) is not int or not 2 <= feature["quantity_one"] <= 256):
                errors.append(f"{path}.quantity_one: expected an integer from 2 to 256")
            if "spacing_one" in feature:
                _expression(feature["spacing_one"], f"{path}.spacing_one", errors)
            second_keys = {"direction_two", "quantity_two", "spacing_two"}
            present_second = second_keys & set(feature)
            if present_second and present_second != second_keys:
                for key in sorted(second_keys - present_second):
                    errors.append(f"{path}: missing required field '{key}' for second direction")
            if "direction_two" in feature:
                if feature["direction_two"] not in AXES:
                    errors.append(f"{path}.direction_two: expected x, y, or z")
                elif feature["direction_two"] == feature.get("direction_one"):
                    errors.append(f"{path}.direction_two: must differ from direction_one")
            if "quantity_two" in feature and (type(feature["quantity_two"]) is not int or not 2 <= feature["quantity_two"] <= 256):
                errors.append(f"{path}.quantity_two: expected an integer from 2 to 256")
            if "spacing_two" in feature:
                _expression(feature["spacing_two"], f"{path}.spacing_two", errors)
        elif feature_type == "circular_pattern":
            for key in ("target_feature", "axis", "quantity", "total_angle"):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
            if "target_feature" in feature and (not isinstance(feature["target_feature"], str) or not feature["target_feature"].strip()):
                errors.append(f"{path}.target_feature: expected a non-empty feature name")
            if feature.get("axis") not in AXES:
                errors.append(f"{path}.axis: expected x, y, or z")
            if "quantity" in feature and (type(feature["quantity"]) is not int or not 2 <= feature["quantity"] <= 256):
                errors.append(f"{path}.quantity: expected an integer from 2 to 256")
            if "total_angle" in feature:
                _expression(feature["total_angle"], f"{path}.total_angle", errors)
            if "symmetric" in feature and not isinstance(feature["symmetric"], bool):
                errors.append(f"{path}.symmetric: expected a boolean")

        if version == "1.1" and isinstance(feature_name, str) and feature_name:
            feature_names.add(feature_name)

    if "result_checks" in plan:
        checks = plan["result_checks"]
        if version == "1.0":
            errors.append("$.result_checks: requires ModelPlan 1.1")
        if not isinstance(checks, dict):
            errors.append("$.result_checks: expected an object")
        else:
            allowed_checks = {"body_count", "required_bodies", "minimum_size", "maximum_size"}
            _unknown_keys(checks, allowed_checks, "$.result_checks", errors)
            if not checks:
                errors.append("$.result_checks: expected at least one check")
            if "body_count" in checks and (type(checks["body_count"]) is not int or not 0 <= checks["body_count"] <= 4096):
                errors.append("$.result_checks.body_count: expected an integer from 0 to 4096")
            if "required_bodies" in checks:
                required_bodies = checks["required_bodies"]
                if (
                    not isinstance(required_bodies, list)
                    or not 1 <= len(required_bodies) <= 256
                    or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in required_bodies)
                ):
                    errors.append("$.result_checks.required_bodies: expected 1 to 256 non-empty body names")
                elif len(required_bodies) != len(set(required_bodies)):
                    errors.append("$.result_checks.required_bodies: duplicate body name")
            for key in ("minimum_size", "maximum_size"):
                if key in checks:
                    _point(checks[key], 3, f"$.result_checks.{key}", errors)

    assumptions = plan.get("assumptions", [])
    if not isinstance(assumptions, list) or len(assumptions) > 64 or any(not isinstance(item, str) or len(item) > 400 for item in assumptions):
        errors.append("$.assumptions: expected up to 64 strings, each at most 400 characters")
    if "source_analysis" in plan and not isinstance(plan["source_analysis"], dict):
        errors.append("$.source_analysis: expected an object")
    return errors


def load_plan(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Path to a ModelPlan JSON file")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2
    errors = validate_plan(plan)
    print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
