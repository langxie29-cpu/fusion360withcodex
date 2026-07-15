"""Typed, validated ModelPlan execution for Fusion 360."""

import json
import math
import threading
import time
import traceback
import uuid

import adsk.core
import adsk.fusion

from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ..mcp_primitives.tool import Tool
from .model_plan_validation import validate_plan

app = adsk.core.Application.get()
MAX_PLAN_BYTES = 256 * 1024
PLAN_TTL_SECONDS = 30 * 60
MAX_STAGED_PLANS = 16
_plan_store = {}
_plan_store_lock = threading.Lock()

APPEARANCE_CANDIDATES = {
    "silver_aluminum": [
        "Aluminum - Satin",
        "Aluminum - Polished",
        "Aluminum - Anodized Glossy (Gray)",
        "Aluminum - Anodized",
    ],
    "silver_glass": [
        "Glass - Frosted",
        "Glass - Window",
        "Paint - Enamel Glossy (White)",
        "Plastic - Glossy (White)",
    ],
    "silver_metal": [
        "Aluminum - Polished",
        "Aluminum - Satin",
        "Chrome - Polished",
    ],
    "black_glass": [
        "Glass - Tinted",
        "Paint - Enamel Glossy (Black)",
        "Plastic - Glossy (Black)",
        "Glass - Window",
    ],
    "black": [
        "Paint - Enamel Glossy (Black)",
        "Plastic - Matte (Black)",
        "Plastic - Glossy (Black)",
    ],
    "flash_white": [
        "LED - White",
        "Glass - Frosted",
        "Paint - Enamel Glossy (White)",
        "Plastic - Glossy (White)",
    ],
}

APPEARANCE_TOKEN_GROUPS = {
    "silver_aluminum": [("aluminum", "satin"), ("铝", "缎"), ("aluminum",), ("铝",)],
    "silver_glass": [("glass", "frost"), ("玻璃", "磨砂"), ("glass",), ("玻璃",)],
    "silver_metal": [("aluminum", "polish"), ("铝", "抛光"), ("aluminum",), ("铝",)],
    "black_glass": [
        ("black", "gloss"),
        ("玻璃", "黑"),
        ("黑色", "光泽"),
        ("black",),
        ("黑色",),
        ("glass", "tint"),
        ("玻璃", "着色"),
    ],
    "black": [("black",), ("黑色",), ("黑",)],
    "flash_white": [("white",), ("白色",), ("glass", "frost"), ("玻璃", "磨砂")],
}


def _result(payload, is_error=False):
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
        "message": payload.get("message", "ModelPlan processed"),
    }


def _operation(name):
    operations = {
        "new_body": adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        "join": adsk.fusion.FeatureOperations.JoinFeatureOperation,
        "cut": adsk.fusion.FeatureOperations.CutFeatureOperation,
        "intersect": adsk.fusion.FeatureOperations.IntersectFeatureOperation,
    }
    return operations[name]


def _direction(name):
    if name == "negative":
        return adsk.fusion.ExtentDirections.NegativeExtentDirection
    return adsk.fusion.ExtentDirections.PositiveExtentDirection


def _appearance_libraries():
    libraries = app.materialLibraries
    try:
        preferred = libraries.itemByName("Fusion Appearance Library")
    except Exception:
        preferred = None
    if preferred:
        yield preferred
    for index in range(libraries.count):
        library = libraries.item(index)
        if preferred and library.id == preferred.id:
            continue
        yield library


def _resolve_appearance(preset):
    candidates = APPEARANCE_CANDIDATES[preset]
    libraries = list(_appearance_libraries())
    for candidate in candidates:
        for library in libraries:
            try:
                appearance = library.appearances.itemByName(candidate)
            except Exception:
                appearance = None
            if appearance:
                return appearance
    for tokens in APPEARANCE_TOKEN_GROUPS[preset]:
        for library in libraries:
            appearances = library.appearances
            for index in range(appearances.count):
                appearance = appearances.item(index)
                name = appearance.name.lower()
                if all(token in name for token in tokens):
                    return appearance
    diagnostics = []
    for library in libraries:
        names = []
        appearances = library.appearances
        for index in range(min(appearances.count, 8)):
            names.append(appearances.item(index).name)
        diagnostics.append({"library": library.name, "count": appearances.count, "samples": names})
    raise RuntimeError(
        f"No Fusion appearance could be resolved for preset: {preset}; "
        f"libraries={json.dumps(diagnostics, ensure_ascii=False)}"
    )


def _apply_appearance(built, preset):
    if not preset:
        return None
    appearance = _resolve_appearance(preset)
    for index in range(built.bodies.count):
        built.bodies.item(index).appearance = appearance
    return appearance.name


def _evaluate(design, expression, positive=False):
    units = design.unitsManager
    if not units.isValidExpression(expression, "cm"):
        raise ValueError(f"Invalid length expression: {expression}")
    value = units.evaluateExpression(expression, "cm")
    if positive and value <= 0:
        raise ValueError(f"Length must be greater than zero: {expression}")
    return value


def _point2(design, point):
    return adsk.core.Point3D.create(
        _evaluate(design, point[0]),
        _evaluate(design, point[1]),
        0,
    )


def _sketch_on_xy(component, design, offset_expression, name):
    offset_value = _evaluate(design, offset_expression)
    plane = component.xYConstructionPlane
    if abs(offset_value) > 1e-9:
        plane_input = component.constructionPlanes.createInput()
        plane_input.setByOffset(plane, adsk.core.ValueInput.createByString(offset_expression))
        plane = component.constructionPlanes.add(plane_input)
        plane.name = f"{name}_offset_plane"
    sketch = component.sketches.add(plane)
    sketch.name = f"{name}_sketch"
    return sketch


def _draw_profile(sketch, design, profile):
    shape = profile["shape"]
    if shape == "rectangle":
        origin = profile["origin"]
        x = _evaluate(design, origin[0])
        y = _evaluate(design, origin[1])
        width = _evaluate(design, profile["width"], positive=True)
        height = _evaluate(design, profile["height"], positive=True)
        sketch.sketchCurves.sketchLines.addTwoPointRectangle(
            adsk.core.Point3D.create(x, y, 0),
            adsk.core.Point3D.create(x + width, y + height, 0),
        )
    elif shape == "circle":
        center = _point2(design, profile["center"])
        radius = _evaluate(design, profile["diameter"], positive=True) / 2
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, radius)
    elif shape == "rounded_rectangle":
        center = _point2(design, profile["center"])
        width = _evaluate(design, profile["width"], positive=True)
        height = _evaluate(design, profile["height"], positive=True)
        radius = _evaluate(design, profile["radius"], positive=True)
        if radius * 2 >= min(width, height):
            raise ValueError("Rounded rectangle radius must be less than half its shortest side")
        left = center.x - width / 2
        right = center.x + width / 2
        bottom = center.y - height / 2
        top = center.y + height / 2
        lines = sketch.sketchCurves.sketchLines
        lines.addByTwoPoints(adsk.core.Point3D.create(left + radius, top, 0), adsk.core.Point3D.create(right - radius, top, 0))
        lines.addByTwoPoints(adsk.core.Point3D.create(right, top - radius, 0), adsk.core.Point3D.create(right, bottom + radius, 0))
        lines.addByTwoPoints(adsk.core.Point3D.create(right - radius, bottom, 0), adsk.core.Point3D.create(left + radius, bottom, 0))
        lines.addByTwoPoints(adsk.core.Point3D.create(left, bottom + radius, 0), adsk.core.Point3D.create(left, top - radius, 0))
        arcs = sketch.sketchCurves.sketchArcs
        quarter_turn = math.pi / 2
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(right - radius, top - radius, 0),
            adsk.core.Point3D.create(right, top - radius, 0),
            quarter_turn,
        )
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(left + radius, top - radius, 0),
            adsk.core.Point3D.create(left + radius, top, 0),
            quarter_turn,
        )
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(left + radius, bottom + radius, 0),
            adsk.core.Point3D.create(left, bottom + radius, 0),
            quarter_turn,
        )
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(right - radius, bottom + radius, 0),
            adsk.core.Point3D.create(right - radius, bottom, 0),
            quarter_turn,
        )
    elif shape == "annulus":
        center = _point2(design, profile["center"])
        outer_radius = _evaluate(design, profile["outer_diameter"], positive=True) / 2
        inner_radius = _evaluate(design, profile["inner_diameter"], positive=True) / 2
        if inner_radius >= outer_radius:
            raise ValueError("Annulus inner diameter must be smaller than outer diameter")
        circles = sketch.sketchCurves.sketchCircles
        circles.addByCenterRadius(center, outer_radius)
        circles.addByCenterRadius(center, inner_radius)
    elif shape == "polyline":
        points = [_point2(design, point) for point in profile["points"]]
        lines = sketch.sketchCurves.sketchLines
        for index, point in enumerate(points):
            lines.addByTwoPoints(point, points[(index + 1) % len(points)])
    else:
        raise ValueError(f"Unsupported profile shape: {shape}")
    if shape == "annulus":
        annular_profiles = []
        for index in range(sketch.profiles.count):
            candidate = sketch.profiles.item(index)
            if candidate.profileLoops.count == 2:
                annular_profiles.append(candidate)
        if len(annular_profiles) != 1:
            raise ValueError(f"Annulus must create exactly one ring region; got {len(annular_profiles)}")
        return annular_profiles[0]
    if sketch.profiles.count != 1:
        raise ValueError(f"Profile must create exactly one closed region; got {sketch.profiles.count}")
    return sketch.profiles.item(0)


def _body_by_name(component, name):
    for index in range(component.bRepBodies.count):
        body = component.bRepBodies.item(index)
        if body.name == name:
            return body
    raise ValueError(f"Target body not found: {name}")


def _edge_fillet(component, design, feature):
    body = _body_by_name(component, feature["target_body"])
    selection = feature["selection"]
    body_bounds = body.boundingBox
    z_min = body_bounds.minPoint.z
    z_max = body_bounds.maxPoint.z
    tolerance = max((z_max - z_min) * 1e-5, 1e-6)
    edges = adsk.core.ObjectCollection.create()
    for index in range(body.edges.count):
        edge = body.edges.item(index)
        bounds = edge.boundingBox
        is_level = abs(bounds.maxPoint.z - bounds.minPoint.z) <= tolerance
        on_top = is_level and abs(bounds.maxPoint.z - z_max) <= tolerance
        on_bottom = is_level and abs(bounds.minPoint.z - z_min) <= tolerance
        include = (
            selection == "all"
            or (selection == "top_perimeter" and on_top)
            or (selection == "bottom_perimeter" and on_bottom)
            or (selection == "top_and_bottom_perimeters" and (on_top or on_bottom))
        )
        if include:
            edges.add(edge)
    if edges.count == 0:
        raise ValueError(f"No edges matched fillet selection '{selection}' on body '{body.name}'")
    fillets = component.features.filletFeatures
    fillet_input = fillets.createInput()
    fillet_input.isRollingBallCorner = True
    edge_set = fillet_input.edgeSetInputs.addConstantRadiusEdgeSet(
        edges,
        adsk.core.ValueInput.createByString(feature["radius"]),
        True,
    )
    edge_set.continuity = adsk.fusion.SurfaceContinuityTypes.TangentSurfaceContinuityType
    built = fillets.add(fillet_input)
    built.name = feature.get("name", feature["id"])
    return built, edges.count


def _extrude(component, profile_or_profiles, distance, operation, name, direction="positive", through_all=False):
    extrudes = component.features.extrudeFeatures
    extrude_input = extrudes.createInput(profile_or_profiles, _operation(operation))
    extent_direction = _direction(direction)
    if through_all:
        try:
            extent = adsk.fusion.ThroughAllExtentDefinition.create()
            extrude_input.setOneSideExtent(extent, extent_direction)
        except Exception:
            extrude_input.setAllExtent(extent_direction)
    else:
        value = adsk.core.ValueInput.createByString(distance)
        try:
            extent = adsk.fusion.DistanceExtentDefinition.create(value)
            extrude_input.setOneSideExtent(extent, extent_direction)
        except Exception:
            if direction == "negative":
                value = adsk.core.ValueInput.createByString(f"-({distance})")
            extrude_input.setDistanceExtent(False, value)
    feature = extrudes.add(extrude_input)
    feature.name = name
    return feature


def _apply_feature(component, design, feature):
    feature_id = feature["id"]
    display_name = feature.get("name", feature_id)
    feature_type = feature["type"]
    operation = "modify" if feature_type == "edge_fillet" else feature.get(
        "operation", "cut" if feature_type == "hole_pattern" else "new_body"
    )
    selected_edge_count = None

    if feature_type == "box":
        origin = feature.get("origin", ["0 cm", "0 cm", "0 cm"])
        profile = {
            "shape": "rectangle",
            "origin": origin[:2],
            "width": feature["width"],
            "height": feature["depth"],
        }
        sketch = _sketch_on_xy(component, design, origin[2], display_name)
        region = _draw_profile(sketch, design, profile)
        built = _extrude(component, region, feature["height"], operation, display_name)
    elif feature_type == "cylinder":
        center = feature.get("center", ["0 cm", "0 cm", "0 cm"])
        profile = {"shape": "circle", "center": center[:2], "diameter": feature["diameter"]}
        sketch = _sketch_on_xy(component, design, center[2], display_name)
        region = _draw_profile(sketch, design, profile)
        built = _extrude(component, region, feature["height"], operation, display_name)
    elif feature_type == "sketch_extrude":
        sketch = _sketch_on_xy(component, design, feature.get("offset", "0 cm"), display_name)
        region = _draw_profile(sketch, design, feature["profile"])
        built = _extrude(
            component,
            region,
            feature["distance"],
            operation,
            display_name,
            feature.get("direction", "positive"),
        )
    elif feature_type == "hole_pattern":
        sketch = _sketch_on_xy(component, design, feature.get("offset", "0 cm"), display_name)
        radius = _evaluate(design, feature["diameter"], positive=True) / 2
        circles = sketch.sketchCurves.sketchCircles
        for point in feature["points"]:
            circles.addByCenterRadius(_point2(design, point), radius)
        if sketch.profiles.count != len(feature["points"]):
            raise ValueError(
                f"Hole pattern created {sketch.profiles.count} regions for {len(feature['points'])} points; "
                "check for overlapping holes"
            )
        regions = adsk.core.ObjectCollection.create()
        for index in range(sketch.profiles.count):
            regions.add(sketch.profiles.item(index))
        through_all = feature["depth"] == "through_all"
        built = _extrude(
            component,
            regions,
            feature["depth"] if not through_all else "1 cm",
            "cut",
            display_name,
            feature.get("direction", "positive"),
            through_all,
        )
    elif feature_type == "edge_fillet":
        built, selected_edge_count = _edge_fillet(component, design, feature)
    else:
        raise ValueError(f"Unsupported feature type: {feature_type}")

    if operation == "new_body":
        for index in range(built.bodies.count):
            suffix = "" if built.bodies.count == 1 else f"_{index + 1}"
            built.bodies.item(index).name = f"{display_name}{suffix}"
    appearance_name = None if feature_type == "edge_fillet" else _apply_appearance(built, feature.get("appearance"))
    return {
        "id": feature_id,
        "type": feature_type,
        "operation": operation,
        "name": display_name,
        "appearance_preset": feature.get("appearance"),
        "appearance_name": appearance_name,
        "selected_edge_count": selected_edge_count,
    }


def _create_design(plan, mode):
    created_document = None
    if mode == "new_document":
        created_document = app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    design = adsk.fusion.Design.cast(app.activeProduct)
    if not design:
        raise RuntimeError("No active Fusion design. Open a Fusion Design document first.")
    try:
        design.designType = adsk.fusion.DesignTypes.ParametricDesignType
    except Exception:
        pass
    # Fusion does not allow renaming the root component. Put generated geometry
    # in a dedicated child component so the plan has a stable, editable name.
    occurrence = design.rootComponent.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    component = occurrence.component
    component.name = plan["component_name"]
    return design, component, created_document


def _add_parameters(design, parameters):
    added = []
    for parameter in parameters:
        name = parameter["name"]
        if design.userParameters.itemByName(name):
            raise ValueError(f"User parameter already exists: {name}")
        try:
            created = design.userParameters.add(
                name,
                adsk.core.ValueInput.createByString(parameter["expression"]),
                parameter["unit"],
                parameter.get("comment", ""),
            )
        except Exception as exc:
            raise ValueError(f"Failed to create user parameter '{name}': {exc}") from exc
        added.append({"name": created.name, "expression": created.expression, "unit": parameter["unit"]})
    return added


def _summary(plan):
    return {
        "component_name": plan["component_name"],
        "parameter_count": len(plan.get("parameters", [])),
        "feature_count": len(plan["features"]),
        "features": [
            {
                "id": item["id"],
                "type": item["type"],
                "operation": "modify" if item["type"] == "edge_fillet" else item.get(
                    "operation", "cut" if item["type"] == "hole_pattern" else "new_body"
                ),
                "appearance": item.get("appearance"),
            }
            for item in plan["features"]
        ],
        "assumptions": plan.get("assumptions", []),
    }


def _stage(plan):
    now = time.time()
    with _plan_store_lock:
        expired = [key for key, value in _plan_store.items() if value["expires_at"] <= now]
        for key in expired:
            _plan_store.pop(key, None)
        while len(_plan_store) >= MAX_STAGED_PLANS:
            oldest = min(_plan_store, key=lambda key: _plan_store[key]["created_at"])
            _plan_store.pop(oldest, None)
        plan_id = uuid.uuid4().hex
        _plan_store[plan_id] = {
            "plan": plan,
            "created_at": now,
            "expires_at": now + PLAN_TTL_SECONDS,
        }
    return plan_id


def stage_handler(plan_json: str) -> dict:
    """Validate and stage a large plan on the HTTP worker thread."""
    created_document = None
    try:
        if not isinstance(plan_json, str):
            return _result({"message": "plan_json must be a JSON string"}, True)
        if len(plan_json.encode("utf-8")) > MAX_PLAN_BYTES:
            return _result({"message": "ModelPlan exceeds the 256 KiB limit"}, True)
        plan = json.loads(plan_json)
        errors = validate_plan(plan)
        if errors:
            return _result({"message": "ModelPlan validation failed", "errors": errors}, True)
        plan_id = _stage(plan)
        return _result({
            "message": "ModelPlan is valid and staged; no Fusion document was changed",
            "plan_id": plan_id,
            "expires_in_seconds": PLAN_TTL_SECONDS,
            **_summary(plan),
        })
    except json.JSONDecodeError as exc:
        return _result({"message": "plan_json is not valid JSON", "detail": str(exc)}, True)
    except Exception as exc:
        return _result({"message": "ModelPlan staging failed", "detail": str(exc)}, True)


def apply_handler(plan_id: str, mode: str = "new_document") -> dict:
    """Apply a previously staged plan on Fusion's main thread."""
    created_document = None
    try:
        if mode not in {"new_document", "active_document"}:
            return _result({"message": "mode must be new_document or active_document"}, True)
        with _plan_store_lock:
            staged = _plan_store.pop(plan_id, None)
        if not staged or staged["expires_at"] <= time.time():
            return _result({"message": "Unknown or expired plan_id; stage the ModelPlan again"}, True)
        plan = staged["plan"]
        errors = validate_plan(plan)
        if errors:
            return _result({"message": "Staged ModelPlan is no longer valid", "errors": errors}, True)
        design, component, created_document = _create_design(plan, mode)
        parameters = _add_parameters(design, plan.get("parameters", []))
        built_features = []
        for feature in plan["features"]:
            try:
                built_features.append(_apply_feature(component, design, feature))
            except Exception as exc:
                raise RuntimeError(f"Feature '{feature['id']}' failed: {exc}") from exc
        app.activeViewport.fit()
        return _result({
            "message": "Fusion model created",
            "plan_id": plan_id,
            "mode": mode,
            "document": app.activeDocument.name if app.activeDocument else None,
            "parameters": parameters,
            "built_features": built_features,
            "body_count": component.bRepBodies.count,
            **_summary(plan),
        })
    except Exception as exc:
        app.log(f"ModelPlan execution failed: {exc}\n{traceback.format_exc()}")
        if created_document is not None:
            try:
                created_document.close(False)
            except Exception:
                pass
        return _result({"message": "ModelPlan execution failed", "detail": str(exc)}, True)


stage_tool = (
    Tool.create_simple(
        name="validate_and_stage_model_plan",
        description=(
            "Validate and stage a complete constrained ModelPlan without changing Fusion. "
            "Returns a short plan_id for apply_staged_model_plan. Always call this before applying."
        ),
    )
    .add_input_property(
        "plan_json",
        {"type": "string", "description": "A complete ModelPlan 1.0 JSON string", "maxLength": MAX_PLAN_BYTES},
    )
    .add_required_input("plan_json")
    .strict_schema()
)

apply_tool = (
    Tool.create_simple(
        name="apply_staged_model_plan",
        description=(
            "Create a Fusion model from a previously validated plan_id. Defaults to a new document; "
            "use active_document only when the user explicitly requested an in-place change."
        ),
    )
    .add_input_property(
        "plan_id",
        {"type": "string", "description": "ID returned by validate_and_stage_model_plan", "minLength": 32, "maxLength": 32},
    )
    .add_input_property(
        "mode",
        {"type": "string", "enum": ["new_document", "active_document"], "default": "new_document"},
    )
    .add_required_input("plan_id")
    .strict_schema()
)

register(Item(primitive=stage_tool, handler=stage_handler, run_on_main_thread=False))
register(Item.create_tool_item(tool=apply_tool, handler=apply_handler))
