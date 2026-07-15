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
TOP_KEYS = {"version", "component_name", "units", "parameters", "features", "assumptions", "source_analysis"}
FEATURE_KEYS = {
    "box": {"id", "type", "name", "operation", "appearance", "origin", "width", "depth", "height"},
    "cylinder": {"id", "type", "name", "operation", "appearance", "center", "diameter", "height"},
    "sketch_extrude": {"id", "type", "name", "operation", "appearance", "plane", "offset", "profile", "distance", "direction"},
    "hole_pattern": {"id", "type", "name", "operation", "appearance", "plane", "offset", "diameter", "points", "depth", "direction"},
    "edge_fillet": {"id", "type", "name", "target_body", "radius", "selection"},
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
    else:
        errors.append(f"{path}.shape: expected rectangle, circle, polyline, rounded_rectangle, or annulus")


def validate_plan(plan: Any) -> list[str]:
    """Return a stable list of validation errors; an empty list means valid."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["$: expected a JSON object"]

    _unknown_keys(plan, TOP_KEYS, "$", errors)
    for key in ("version", "component_name", "units", "features"):
        if key not in plan:
            errors.append(f"$: missing required field '{key}'")

    if plan.get("version") != "1.0":
        errors.append("$.version: expected '1.0'")
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
        _unknown_keys(feature, allowed, path, errors)
        feature_id = feature.get("id")
        if not isinstance(feature_id, str) or not ID_RE.fullmatch(feature_id):
            errors.append(f"{path}.id: expected an ASCII feature identifier")
        elif feature_id in feature_ids:
            errors.append(f"{path}.id: duplicate feature id '{feature_id}'")
        else:
            feature_ids.add(feature_id)

        if feature_type == "edge_fillet":
            operation = None
            if not body_exists:
                errors.append(f"{path}: edge_fillet requires an earlier body")
            target_body = feature.get("target_body")
            if isinstance(target_body, str) and target_body.strip() and target_body not in body_names:
                errors.append(f"{path}.target_body: no earlier new_body named '{target_body}'")
        else:
            default_operation = "cut" if feature_type == "hole_pattern" else "new_body"
            operation = feature.get("operation", default_operation)
            if operation not in OPERATIONS:
                errors.append(f"{path}.operation: unsupported operation '{operation}'")
            if feature_type == "hole_pattern" and operation != "cut":
                errors.append(f"{path}.operation: hole_pattern must use cut")
        if "appearance" in feature and feature["appearance"] not in APPEARANCE_PRESETS:
            errors.append(f"{path}.appearance: unsupported appearance preset '{feature['appearance']}'")
        if feature_type != "edge_fillet" and operation != "new_body" and not body_exists:
            errors.append(f"{path}.operation: '{operation}' requires an earlier body")
        if operation == "new_body":
            body_exists = True
            body_name = feature.get("name", feature_id)
            if isinstance(body_name, str) and body_name:
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
        elif feature_type == "sketch_extrude":
            if feature.get("plane", "xy") != "xy":
                errors.append(f"{path}.plane: MVP supports only xy")
            if "profile" not in feature:
                errors.append(f"{path}: missing required field 'profile'")
            else:
                _profile(feature["profile"], f"{path}.profile", errors)
            if "distance" not in feature:
                errors.append(f"{path}: missing required field 'distance'")
            else:
                _expression(feature["distance"], f"{path}.distance", errors)
            if "offset" in feature:
                _expression(feature["offset"], f"{path}.offset", errors)
            if feature.get("direction", "positive") not in {"positive", "negative"}:
                errors.append(f"{path}.direction: expected positive or negative")
        elif feature_type == "hole_pattern":
            if feature.get("plane", "xy") != "xy":
                errors.append(f"{path}.plane: MVP supports only xy")
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
        elif feature_type == "edge_fillet":
            for key in ("target_body", "radius", "selection"):
                if key not in feature:
                    errors.append(f"{path}: missing required field '{key}'")
            if "target_body" in feature and (not isinstance(feature["target_body"], str) or not feature["target_body"].strip()):
                errors.append(f"{path}.target_body: expected a non-empty body name")
            if "radius" in feature:
                _expression(feature["radius"], f"{path}.radius", errors)
            if feature.get("selection") not in {"top_perimeter", "bottom_perimeter", "top_and_bottom_perimeters", "all"}:
                errors.append(f"{path}.selection: unsupported edge selection")

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
