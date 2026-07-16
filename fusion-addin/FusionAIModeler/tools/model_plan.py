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


def _evaluate_angle(design, expression):
    units = design.unitsManager
    if not units.isValidExpression(expression, "deg"):
        raise ValueError(f"Invalid angle expression: {expression}")
    value = units.evaluateExpression(expression, "rad")
    if value <= 0 or value >= math.pi / 2:
        raise ValueError(f"Angle must be greater than 0 deg and less than 90 deg: {expression}")
    return value


def _point3_values(design, point):
    return tuple(_evaluate(design, coordinate) for coordinate in point)


def _as_point3(value):
    return adsk.core.Point3D.create(value[0], value[1], value[2])


def _as_vector3(value):
    return adsk.core.Vector3D.create(value[0], value[1], value[2])


def _vector_add(left, right):
    return tuple(left[index] + right[index] for index in range(3))


def _vector_sub(left, right):
    return tuple(left[index] - right[index] for index in range(3))


def _vector_scale(value, scale):
    return tuple(coordinate * scale for coordinate in value)


def _vector_length(value):
    return math.sqrt(sum(coordinate * coordinate for coordinate in value))


def _vector_unit(value):
    length = _vector_length(value)
    if length <= 1e-9:
        raise ValueError("Zero-length direction is not allowed")
    return tuple(coordinate / length for coordinate in value)


def _vector_cross(left, right):
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _temp_cylinder(manager, point_one, point_two, diameter_one, diameter_two=None):
    diameter_two = diameter_one if diameter_two is None else diameter_two
    body = manager.createCylinderOrCone(
        _as_point3(point_one),
        diameter_one / 2,
        _as_point3(point_two),
        diameter_two / 2,
    )
    if not body:
        raise RuntimeError("Fusion failed to create a temporary cylinder or cone")
    return body


def _temp_box(manager, center, length_direction, width_direction, length, width, height):
    oriented_box = adsk.core.OrientedBoundingBox3D.create(
        _as_point3(center),
        _as_vector3(_vector_unit(length_direction)),
        _as_vector3(_vector_unit(width_direction)),
        length,
        width,
        height,
    )
    body = manager.createBox(oriented_box)
    if not body:
        raise RuntimeError("Fusion failed to create a temporary oriented box")
    return body


def _temp_hex_prism(manager, point_one, point_two, across_flats):
    axis = _vector_unit(_vector_sub(point_two, point_one))
    reference = (0.0, 0.0, 1.0)
    if abs(sum(axis[index] * reference[index] for index in range(3))) > 0.95:
        reference = (0.0, 1.0, 0.0)
    basis_one = _vector_unit(_vector_cross(axis, reference))
    basis_two = _vector_unit(_vector_cross(axis, basis_one))
    center = _vector_scale(_vector_add(point_one, point_two), 0.5)
    length = _vector_length(_vector_sub(point_two, point_one))
    result = None
    for index in range(3):
        angle = index * math.pi / 3
        width_direction = _vector_add(
            _vector_scale(basis_one, math.cos(angle)),
            _vector_scale(basis_two, math.sin(angle)),
        )
        strip = _temp_box(
            manager,
            center,
            axis,
            width_direction,
            length,
            across_flats * 3,
            across_flats,
        )
        if result is None:
            result = strip
        elif not manager.booleanOperation(
            result,
            strip,
            adsk.fusion.BooleanTypes.IntersectionBooleanType,
        ):
            raise RuntimeError("Fusion failed to intersect the temporary hex-prism strips")
    return result


def _temp_difference(manager, target, tool, label):
    if not manager.booleanOperation(target, tool, adsk.fusion.BooleanTypes.DifferenceBooleanType):
        raise RuntimeError(f"Fusion failed to cut temporary body: {label}")


def _temp_union(manager, target, tool, label):
    if not manager.booleanOperation(target, tool, adsk.fusion.BooleanTypes.UnionBooleanType):
        raise RuntimeError(f"Fusion failed to join temporary body: {label}")


def _mecanum_drive(component, design, feature):
    """Build one bounded wheel-coupler-motor-bracket assembly.

    The generated bodies live in a named BaseFeature. This keeps the feature
    bounded and editable while allowing the arbitrary roller axes that the
    ModelPlan sketch primitives cannot represent.
    """
    display_name = feature.get("name", feature["id"])
    center = _point3_values(design, feature["center"])
    side = feature["side"]
    handedness = feature["handedness"]
    inward = 1.0 if side == "left" else -1.0
    handed_sign = 1.0 if handedness == "A" else -1.0
    axis = (1.0, 0.0, 0.0)

    wheel_fields = (
        "diameter", "width", "roller_diameter", "roller_end_diameter", "roller_length",
        "roller_axle_diameter", "roller_axle_overhang", "hub_diameter", "hub_width",
        "flange_diameter", "flange_thickness", "hex_socket_depth", "spoke_inner_diameter",
        "spoke_width", "spoke_thickness",
    )
    wheel = {key: _evaluate(design, feature["wheel"][key], positive=True) for key in wheel_fields}
    wheel["roller_angle"] = _evaluate_angle(design, feature["wheel"]["roller_angle"])
    wheel["roller_count"] = feature["wheel"]["roller_count"]
    if wheel["roller_end_diameter"] >= wheel["roller_diameter"]:
        raise ValueError("wheel.roller_end_diameter must be smaller than wheel.roller_diameter")
    if wheel["hub_width"] + 2 * wheel["flange_thickness"] > wheel["width"] + 1e-6:
        raise ValueError("wheel hub and flange stack exceeds wheel.width")
    if wheel["flange_diameter"] >= wheel["diameter"]:
        raise ValueError("wheel.flange_diameter must be smaller than wheel.diameter")
    roller_axial_envelope = (
        wheel["roller_length"] * math.cos(wheel["roller_angle"])
        + wheel["roller_diameter"] * math.sin(wheel["roller_angle"])
    )
    if roller_axial_envelope > wheel["width"] + 1e-6:
        raise ValueError("Wheel roller axial envelope exceeds wheel.width")
    axle_projection = (
        wheel["roller_length"] + 2 * wheel["roller_axle_overhang"]
    ) * math.cos(wheel["roller_angle"])
    required_axle_projection = wheel["width"] - wheel["spoke_thickness"]
    if axle_projection + 1e-6 < required_axle_projection:
        raise ValueError("Wheel roller axle does not reach both side-support center planes")

    coupler_fields = (
        "hex_af", "hex_length", "barrel_diameter", "barrel_length", "bore_diameter",
        "set_screw_diameter", "retention_thread_diameter", "shaft_engagement",
    )
    coupler = {key: _evaluate(design, feature["coupler"][key], positive=True) for key in coupler_fields}
    if coupler["hex_length"] > wheel["hex_socket_depth"] + 1e-6:
        raise ValueError("coupler.hex_length exceeds wheel.hex_socket_depth")

    motor_fields = (
        "shaft_diameter", "shaft_flat", "shaft_length", "boss_diameter", "boss_length",
        "gearbox_diameter", "gearbox_length", "can_diameter", "can_length",
        "encoder_diameter", "encoder_length", "mount_pcd", "mount_thread_diameter",
        "mount_thread_depth",
    )
    motor = {key: _evaluate(design, feature["motor"][key], positive=True) for key in motor_fields}
    motor["mount_hole_count"] = feature["motor"]["mount_hole_count"]
    if motor["shaft_flat"] >= motor["shaft_diameter"]:
        raise ValueError("motor.shaft_flat must be smaller than motor.shaft_diameter")
    if coupler["shaft_engagement"] > min(coupler["barrel_length"], motor["shaft_length"]):
        raise ValueError("coupler.shaft_engagement exceeds the barrel or motor shaft length")
    if motor["shaft_length"] - coupler["shaft_engagement"] < motor["boss_length"] - 1e-6:
        raise ValueError("Coupler barrel would collide axially with the motor locating boss")

    bracket_fields = (
        "plate_thickness", "base_length", "width", "height", "axis_height",
        "boss_clearance", "keyhole_head_diameter", "keyhole_slot_width",
        "keyhole_slot_length", "base_hole_diameter", "base_hole_near",
        "base_hole_far", "base_hole_pitch_y",
    )
    bracket = {key: _evaluate(design, feature["bracket"][key], positive=True) for key in bracket_fields}
    if bracket["axis_height"] >= bracket["height"]:
        raise ValueError("bracket.axis_height must be smaller than bracket.height")
    if bracket["base_hole_far"] >= bracket["base_length"]:
        raise ValueError("bracket.base_hole_far must be inside bracket.base_length")

    hardware_fields = (
        "motor_bolt_diameter", "motor_bolt_head_diameter", "motor_bolt_head_thickness",
        "base_bolt_diameter", "base_bolt_length", "base_bolt_head_diameter",
        "base_bolt_head_thickness", "wheel_screw_diameter", "wheel_screw_length",
        "wheel_screw_head_diameter", "wheel_screw_head_thickness",
    )
    hardware = {key: _evaluate(design, feature["hardware"][key], positive=True) for key in hardware_fields}
    hardware["motor_bolt_count"] = feature["hardware"]["motor_bolt_count"]
    if hardware["motor_bolt_count"] > motor["mount_hole_count"]:
        raise ValueError("hardware.motor_bolt_count exceeds motor.mount_hole_count")
    minimum_wheel_screw_length = (
        wheel["width"] - coupler["hex_length"] + coupler["retention_thread_diameter"]
    )
    if hardware["wheel_screw_length"] + 1e-6 < minimum_wheel_screw_length:
        raise ValueError("Wheel-retention screw is too short for one-diameter thread engagement")

    appearances = {}
    for key, preset in feature["appearances"].items():
        appearances[key] = _resolve_appearance(preset)
    manager = adsk.fusion.TemporaryBRepManager.get()
    base_feature = component.features.baseFeatures.add()
    base_feature.name = display_name
    if not base_feature.startEdit():
        raise RuntimeError("Fusion failed to enter BaseFeature edit mode")
    body_count = 0

    def persist(temp_body, local_name, appearance_key):
        nonlocal body_count
        body = component.bRepBodies.add(temp_body, base_feature)
        if not body:
            raise RuntimeError(f"Fusion failed to persist body: {local_name}")
        body.name = f"{display_name}_{local_name}"
        body.appearance = appearances[appearance_key]
        body_count += 1
        return body

    cx, cy, cz = center
    inner_face_x = cx + inward * wheel["width"] / 2
    outer_face_x = cx - inward * wheel["width"] / 2
    roller_center_radius = wheel["diameter"] / 2 - wheel["roller_diameter"] / 2
    if roller_center_radius <= wheel["flange_diameter"] / 2:
        raise ValueError("Wheel rollers do not clear the hub flange")

    try:
        # Wheel hub, two face flanges, exact 12 mm-class hex socket, and axial screw clearance.
        hub_start = (cx - wheel["hub_width"] / 2, cy, cz)
        hub_end = (cx + wheel["hub_width"] / 2, cy, cz)
        hub = _temp_cylinder(manager, hub_start, hub_end, wheel["hub_diameter"])
        inner_flange = _temp_cylinder(
            manager,
            (inner_face_x - inward * wheel["flange_thickness"], cy, cz),
            (inner_face_x, cy, cz),
            wheel["flange_diameter"],
        )
        outer_flange = _temp_cylinder(
            manager,
            (outer_face_x, cy, cz),
            (outer_face_x + inward * wheel["flange_thickness"], cy, cz),
            wheel["flange_diameter"],
        )
        axial_clearance = _temp_cylinder(
            manager,
            (outer_face_x - inward * 0.1, cy, cz),
            (inner_face_x + inward * 0.1, cy, cz),
            coupler["retention_thread_diameter"] + _evaluate(design, "0.2 mm"),
        )
        for label, body in (("hub", hub), ("inner_flange", inner_flange), ("outer_flange", outer_flange)):
            _temp_difference(manager, body, axial_clearance, f"{label} axial M4 clearance")
        socket_end_x = inner_face_x - inward * wheel["hex_socket_depth"]
        hex_socket = _temp_hex_prism(
            manager,
            (inner_face_x + inward * 0.1, cy, cz),
            (socket_end_x - inward * 0.1, cy, cz),
            coupler["hex_af"] + _evaluate(design, "0.2 mm"),
        )
        _temp_difference(manager, inner_flange, hex_socket, "wheel inner-flange hex socket")
        _temp_difference(manager, hub, hex_socket, "wheel hub hex socket")
        persist(hub, "Wheel_Hub", "hub")
        persist(inner_flange, "Wheel_Inner_Flange", "hub")
        persist(outer_flange, "Wheel_Outer_Flange", "hub")

        # Face spokes and nine barrel-shaped rollers. A/B hands form the X-pattern in the plan.
        spoke_start_radius = wheel["spoke_inner_diameter"] / 2
        spoke_length = roller_center_radius - spoke_start_radius
        if spoke_length <= 0:
            raise ValueError("wheel.spoke_inner_diameter is too large")
        face_x_values = (
            outer_face_x + inward * wheel["spoke_thickness"] / 2,
            inner_face_x - inward * wheel["spoke_thickness"] / 2,
        )
        for index in range(wheel["roller_count"]):
            theta = 2 * math.pi * index / wheel["roller_count"]
            radial = (0.0, math.cos(theta), math.sin(theta))
            tangent = (0.0, -math.sin(theta), math.cos(theta))
            spoke_radius = spoke_start_radius + spoke_length / 2
            for face_index, face_x in enumerate(face_x_values, start=1):
                spoke_center = (
                    face_x,
                    cy + radial[1] * spoke_radius,
                    cz + radial[2] * spoke_radius,
                )
                spoke = _temp_box(
                    manager,
                    spoke_center,
                    radial,
                    tangent,
                    spoke_length,
                    wheel["spoke_width"],
                    wheel["spoke_thickness"],
                )
                persist(spoke, f"Wheel_Spoke_{face_index}_{index + 1:02d}", "hub")

            roller_center = (
                cx,
                cy + radial[1] * roller_center_radius,
                cz + radial[2] * roller_center_radius,
            )
            roller_axis = _vector_unit(_vector_add(
                _vector_scale(axis, math.cos(wheel["roller_angle"])),
                _vector_scale(tangent, handed_sign * math.sin(wheel["roller_angle"])),
            ))
            axle_half = wheel["roller_length"] / 2 + wheel["roller_axle_overhang"]
            axle = _temp_cylinder(
                manager,
                _vector_add(roller_center, _vector_scale(roller_axis, -axle_half)),
                _vector_add(roller_center, _vector_scale(roller_axis, axle_half)),
                wheel["roller_axle_diameter"],
            )
            persist(axle, f"Roller_Axle_{index + 1:02d}", "axle")

            positions = (-0.5, -0.3, 0.3, 0.5)
            roller_points = [
                _vector_add(roller_center, _vector_scale(roller_axis, wheel["roller_length"] * value))
                for value in positions
            ]
            roller = _temp_cylinder(
                manager,
                roller_points[0],
                roller_points[1],
                wheel["roller_end_diameter"],
                wheel["roller_diameter"],
            )
            roller_middle = _temp_cylinder(
                manager,
                roller_points[1],
                roller_points[2],
                wheel["roller_diameter"],
            )
            roller_end = _temp_cylinder(
                manager,
                roller_points[2],
                roller_points[3],
                wheel["roller_diameter"],
                wheel["roller_end_diameter"],
            )
            _temp_union(manager, roller, roller_middle, f"roller {index + 1} center")
            _temp_union(manager, roller, roller_end, f"roller {index + 1} end")
            persist(roller, f"Mecanum_Roller_{handedness}_{index + 1:02d}", "roller")

        # Wheel-side hex, D6 barrel, radial M4 set screw, and axial wheel-retention screw.
        hex_end_x = inner_face_x - inward * coupler["hex_length"]
        hex_head = _temp_hex_prism(
            manager,
            (inner_face_x, cy, cz),
            (hex_end_x, cy, cz),
            coupler["hex_af"],
        )
        hex_thread = _temp_cylinder(
            manager,
            (inner_face_x + inward * 0.1, cy, cz),
            (hex_end_x - inward * 0.1, cy, cz),
            coupler["retention_thread_diameter"],
        )
        _temp_difference(manager, hex_head, hex_thread, "coupler axial M4 thread")
        persist(hex_head, "Coupler_Hex_Head_11p9AF", "coupler")

        barrel_end_x = inner_face_x + inward * coupler["barrel_length"]
        barrel = _temp_cylinder(
            manager,
            (inner_face_x, cy, cz),
            (barrel_end_x, cy, cz),
            coupler["barrel_diameter"],
        )
        barrel_bore = _temp_cylinder(
            manager,
            (inner_face_x - inward * 0.1, cy, cz),
            (barrel_end_x + inward * 0.1, cy, cz),
            coupler["bore_diameter"],
        )
        _temp_difference(manager, barrel, barrel_bore, "coupler D6 bore")
        set_screw_x = inner_face_x + inward * coupler["barrel_length"] * 0.65
        set_screw_hole = _temp_cylinder(
            manager,
            (set_screw_x, cy, cz + coupler["barrel_diameter"]),
            (set_screw_x, cy, cz - coupler["barrel_diameter"]),
            coupler["set_screw_diameter"],
        )
        _temp_difference(manager, barrel, set_screw_hole, "coupler radial M4 set-screw hole")
        persist(barrel, "Coupler_Barrel_D6", "coupler")
        set_screw = _temp_cylinder(
            manager,
            (set_screw_x, cy, cz + coupler["barrel_diameter"] / 2 + _evaluate(design, "2 mm")),
            (
                set_screw_x,
                cy,
                cz - motor["shaft_diameter"] / 2 + motor["shaft_flat"],
            ),
            coupler["set_screw_diameter"],
        )
        persist(set_screw, "Hardware_M4_Set_Screw", "hardware")

        wheel_screw_end_x = outer_face_x + inward * hardware["wheel_screw_length"]
        wheel_screw = _temp_cylinder(
            manager,
            (outer_face_x, cy, cz),
            (wheel_screw_end_x, cy, cz),
            hardware["wheel_screw_diameter"],
        )
        persist(wheel_screw, "Hardware_M4_Wheel_Retention_Screw", "hardware")
        wheel_head = _temp_cylinder(
            manager,
            (outer_face_x - inward * hardware["wheel_screw_head_thickness"], cy, cz),
            (outer_face_x, cy, cz),
            hardware["wheel_screw_head_diameter"],
        )
        persist(wheel_head, "Hardware_M4_Wheel_Retention_Head", "hardware")

        # Coaxial D-shaft engagement and the dimensioned JGB37-520 107 RPM motor stack.
        gearbox_front_x = barrel_end_x + inward * (motor["shaft_length"] - coupler["shaft_engagement"])
        shaft_end_x = gearbox_front_x - inward * motor["shaft_length"]
        shaft = _temp_cylinder(
            manager,
            (shaft_end_x, cy, cz),
            (gearbox_front_x, cy, cz),
            motor["shaft_diameter"],
        )
        shaft_radius = motor["shaft_diameter"] / 2
        flat_z = cz - shaft_radius + motor["shaft_flat"]
        flat_tool = _temp_box(
            manager,
            ((shaft_end_x + gearbox_front_x) / 2, cy, flat_z + motor["shaft_diameter"] / 2),
            axis,
            (0.0, 1.0, 0.0),
            motor["shaft_length"] + _evaluate(design, "0.2 mm"),
            motor["shaft_diameter"] * 2,
            motor["shaft_diameter"],
        )
        _temp_difference(manager, shaft, flat_tool, "motor D-shaft flat")
        persist(shaft, "Motor_D6_Shaft", "axle")
        boss = _temp_cylinder(
            manager,
            (gearbox_front_x - inward * motor["boss_length"], cy, cz),
            (gearbox_front_x, cy, cz),
            motor["boss_diameter"],
        )
        persist(boss, "Motor_Front_Boss_D12", "motor")
        pcd_radius = motor["mount_pcd"] / 2
        slot_angle = bracket["keyhole_slot_length"] / pcd_radius
        mount_centers = []
        for index in range(motor["mount_hole_count"]):
            bolt_phi = 2 * math.pi * index / motor["mount_hole_count"] + slot_angle
            mount_centers.append((
                cx,
                cy + math.cos(bolt_phi) * pcd_radius,
                cz + math.sin(bolt_phi) * pcd_radius,
            ))
        gearbox_end_x = gearbox_front_x + inward * motor["gearbox_length"]
        gearbox = _temp_cylinder(
            manager,
            (gearbox_front_x, cy, cz),
            (gearbox_end_x, cy, cz),
            motor["gearbox_diameter"],
        )
        for index, mount_center in enumerate(mount_centers, start=1):
            threaded_hole = _temp_cylinder(
                manager,
                (gearbox_front_x - inward * 0.1, mount_center[1], mount_center[2]),
                (
                    gearbox_front_x + inward * (motor["mount_thread_depth"] + 0.1),
                    mount_center[1],
                    mount_center[2],
                ),
                motor["mount_thread_diameter"],
            )
            _temp_difference(manager, gearbox, threaded_hole, f"gearbox M3 threaded hole {index}")
        persist(gearbox, "Motor_Gearbox_D37_L24", "motor")
        can_end_x = gearbox_end_x + inward * motor["can_length"]
        motor_can = _temp_cylinder(
            manager,
            (gearbox_end_x, cy, cz),
            (can_end_x, cy, cz),
            motor["can_diameter"],
        )
        persist(motor_can, "Motor_Can_D32_L22", "motor")
        encoder_end_x = can_end_x + inward * motor["encoder_length"]
        encoder = _temp_cylinder(
            manager,
            (can_end_x, cy, cz),
            (encoder_end_x, cy, cz),
            motor["encoder_diameter"],
        )
        persist(encoder, "Motor_AB_Encoder", "encoder")

        # Dimensioned 40 x 40 x 47 mm L-bracket with U-clearance, six keyholes, and four base holes.
        bracket_outer_x = gearbox_front_x - inward * bracket["plate_thickness"]
        bracket_base_z = cz - bracket["axis_height"]
        wall = _temp_box(
            manager,
            ((bracket_outer_x + gearbox_front_x) / 2, cy, bracket_base_z + bracket["height"] / 2),
            axis,
            (0.0, 1.0, 0.0),
            bracket["plate_thickness"],
            bracket["width"],
            bracket["height"],
        )
        wall_through_start = bracket_outer_x - inward * 0.1
        wall_through_end = gearbox_front_x + inward * 0.1
        u_round = _temp_cylinder(
            manager,
            (wall_through_start, cy, cz),
            (wall_through_end, cy, cz),
            bracket["boss_clearance"],
        )
        _temp_difference(manager, wall, u_round, "bracket U-slot round top")
        u_stem = _temp_box(
            manager,
            ((wall_through_start + wall_through_end) / 2, cy, bracket_base_z + bracket["axis_height"] / 2),
            axis,
            (0.0, 1.0, 0.0),
            abs(wall_through_end - wall_through_start),
            bracket["boss_clearance"],
            bracket["axis_height"],
        )
        _temp_difference(manager, wall, u_stem, "bracket U-slot stem")
        for index in range(motor["mount_hole_count"]):
            phi = 2 * math.pi * index / motor["mount_hole_count"]
            radial = (0.0, math.cos(phi), math.sin(phi))
            hole_center = (cx, cy + radial[1] * pcd_radius, cz + radial[2] * pcd_radius)
            keyhole = _temp_cylinder(
                manager,
                (wall_through_start, hole_center[1], hole_center[2]),
                (wall_through_end, hole_center[1], hole_center[2]),
                bracket["keyhole_head_diameter"],
            )
            _temp_difference(manager, wall, keyhole, f"bracket keyhole {index + 1} head")
            # Rotate the final M3 shank position along the D31 pitch circle so
            # the head enters through D6.3 and clamps in the D3.2 keyhole slot.
            bolt_center = mount_centers[index]
            slot_vector = _vector_sub(bolt_center, hole_center)
            slot_length = _vector_length(slot_vector)
            slot_center = _vector_scale(_vector_add(hole_center, bolt_center), 0.5)
            slot = _temp_box(
                manager,
                ((wall_through_start + wall_through_end) / 2, slot_center[1], slot_center[2]),
                axis,
                slot_vector,
                abs(wall_through_end - wall_through_start),
                slot_length + bracket["keyhole_slot_width"],
                bracket["keyhole_slot_width"],
            )
            _temp_difference(manager, wall, slot, f"bracket keyhole {index + 1} slot")
        persist(wall, "Bracket_Vertical_Plate", "bracket")

        base_center_x = gearbox_front_x + inward * bracket["base_length"] / 2
        bracket_base = _temp_box(
            manager,
            (base_center_x, cy, bracket_base_z + bracket["plate_thickness"] / 2),
            axis,
            (0.0, 1.0, 0.0),
            bracket["base_length"],
            bracket["width"],
            bracket["plate_thickness"],
        )
        base_hole_centers = []
        for distance in (bracket["base_hole_near"], bracket["base_hole_far"]):
            for y_sign in (-1.0, 1.0):
                hole_center = (
                    gearbox_front_x + inward * distance,
                    cy + y_sign * bracket["base_hole_pitch_y"] / 2,
                    bracket_base_z,
                )
                base_hole_centers.append(hole_center)
                base_hole = _temp_cylinder(
                    manager,
                    (hole_center[0], hole_center[1], bracket_base_z - 0.1),
                    (hole_center[0], hole_center[1], bracket_base_z + bracket["plate_thickness"] + 0.1),
                    bracket["base_hole_diameter"],
                )
                _temp_difference(manager, bracket_base, base_hole, "bracket base M3.5 hole")
        persist(bracket_base, "Bracket_Base_Plate", "bracket")

        # Planned fasteners: three alternating motor screws and four chassis screws per bracket.
        mount_step = max(1, motor["mount_hole_count"] // hardware["motor_bolt_count"])
        for bolt_index in range(hardware["motor_bolt_count"]):
            center_index = min(bolt_index * mount_step, len(mount_centers) - 1)
            hole_center = mount_centers[center_index]
            bolt_start_x = bracket_outer_x - inward * hardware["motor_bolt_head_thickness"]
            bolt_end_x = gearbox_front_x + inward * motor["mount_thread_depth"]
            bolt = _temp_cylinder(
                manager,
                (bolt_start_x, hole_center[1], hole_center[2]),
                (bolt_end_x, hole_center[1], hole_center[2]),
                hardware["motor_bolt_diameter"],
            )
            persist(bolt, f"Hardware_M3_Motor_Bolt_{bolt_index + 1}", "hardware")
            bolt_head = _temp_cylinder(
                manager,
                (bolt_start_x, hole_center[1], hole_center[2]),
                (bracket_outer_x, hole_center[1], hole_center[2]),
                hardware["motor_bolt_head_diameter"],
            )
            persist(bolt_head, f"Hardware_M3_Motor_Head_{bolt_index + 1}", "hardware")

        for bolt_index, hole_center in enumerate(base_hole_centers, start=1):
            bolt_top_z = bracket_base_z + bracket["plate_thickness"]
            bolt_bottom_z = bolt_top_z - hardware["base_bolt_length"]
            bolt = _temp_cylinder(
                manager,
                (hole_center[0], hole_center[1], bolt_bottom_z),
                (hole_center[0], hole_center[1], bolt_top_z),
                hardware["base_bolt_diameter"],
            )
            persist(bolt, f"Hardware_M3_Base_Bolt_{bolt_index}", "hardware")
            bolt_head = _temp_cylinder(
                manager,
                (hole_center[0], hole_center[1], bolt_top_z),
                (hole_center[0], hole_center[1], bolt_top_z + hardware["base_bolt_head_thickness"]),
                hardware["base_bolt_head_diameter"],
            )
            persist(bolt_head, f"Hardware_M3_Base_Head_{bolt_index}", "hardware")
    finally:
        base_feature.finishEdit()

    # Result-body names and appearances normally propagate from source bodies.
    # Reassert the count after leaving edit mode so a failed persistence is visible.
    if base_feature.bodies.count != body_count:
        raise RuntimeError(
            f"Mecanum drive persisted {body_count} source bodies but Fusion reports "
            f"{base_feature.bodies.count} result bodies"
        )
    return base_feature, body_count


def _point2(design, point):
    return adsk.core.Point3D.create(
        _evaluate(design, point[0]),
        _evaluate(design, point[1]),
        0,
    )


def _base_plane(component, plane_name):
    planes = {
        "xy": component.xYConstructionPlane,
        "xz": component.xZConstructionPlane,
        "yz": component.yZConstructionPlane,
    }
    return planes[plane_name]


def _sketch_on_plane(component, design, offset_expression, name, plane_name="xy"):
    offset_value = _evaluate(design, offset_expression)
    plane = _base_plane(component, plane_name)
    if abs(offset_value) > 1e-9:
        plane_input = component.constructionPlanes.createInput()
        plane_input.setByOffset(plane, adsk.core.ValueInput.createByString(offset_expression))
        plane = component.constructionPlanes.add(plane_input)
        plane.name = f"{name}_{plane_name}_offset_plane"
    sketch = component.sketches.add(plane)
    sketch.name = f"{name}_sketch"
    return sketch


def _opposite_direction(direction):
    return "negative" if direction == "positive" else "positive"


def _axis_sketch(component, design, center_expressions, axis, name, direction):
    """Create a sketch normal to a global axis and return its local center.

    Fusion's built-in construction planes do not all expose the same positive
    normal. Deriving both the signed offset and extrusion direction from the
    plane geometry keeps cylinder.axis aligned with the requested global axis.
    """
    plane_name = {"x": "yz", "y": "xz", "z": "xy"}[axis]
    base_plane = _base_plane(component, plane_name)
    plane_geometry = base_plane.geometry
    normal = plane_geometry.normal
    axis_index = {"x": 0, "y": 1, "z": 2}[axis]
    normal_component = (normal.x, normal.y, normal.z)[axis_index]
    if abs(normal_component) < 0.5:
        raise RuntimeError(f"Fusion construction plane '{plane_name}' is not normal to axis '{axis}'")

    center = _point3_values(design, center_expressions)
    origin = plane_geometry.origin
    origin_coordinate = (origin.x, origin.y, origin.z)[axis_index]
    signed_offset = (center[axis_index] - origin_coordinate) / normal_component
    plane = base_plane
    if abs(signed_offset) > 1e-9:
        plane_input = component.constructionPlanes.createInput()
        plane_input.setByOffset(plane, adsk.core.ValueInput.createByReal(signed_offset))
        plane = component.constructionPlanes.add(plane_input)
        plane.name = f"{name}_{plane_name}_offset_plane"
    sketch = component.sketches.add(plane)
    sketch.name = f"{name}_sketch"
    local_center = sketch.modelToSketchSpace(_as_point3(center))
    global_direction = direction if normal_component > 0 else _opposite_direction(direction)
    return sketch, local_center, global_direction


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
    elif shape == "slot":
        center = _point2(design, profile["center"])
        overall_length = _evaluate(design, profile["length"], positive=True)
        width = _evaluate(design, profile["width"], positive=True)
        if overall_length <= width:
            raise ValueError("Slot length must be greater than its width")
        angle_expression = profile.get("angle", "0 deg")
        units = design.unitsManager
        if not units.isValidExpression(angle_expression, "deg"):
            raise ValueError(f"Invalid slot angle expression: {angle_expression}")
        angle = units.evaluateExpression(angle_expression, "rad")
        half_center_distance = (overall_length - width) / 2
        offset_x = math.cos(angle) * half_center_distance
        offset_y = math.sin(angle) * half_center_distance
        start = adsk.core.Point3D.create(center.x - offset_x, center.y - offset_y, 0)
        end = adsk.core.Point3D.create(center.x + offset_x, center.y + offset_y, 0)
        sketch.addCenterToCenterSlot(
            start,
            end,
            adsk.core.ValueInput.createByString(profile["width"]),
        )
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


def _selected_edges(component, target_body, selection, operation_name):
    body = _body_by_name(component, target_body)
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
        raise ValueError(
            f"No edges matched {operation_name} selection '{selection}' on body '{body.name}'"
        )
    return edges


def _edge_fillet(component, design, feature):
    edges = _selected_edges(
        component,
        feature["target_body"],
        feature["selection"],
        "fillet",
    )
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


def _edge_chamfer(component, feature):
    edges = _selected_edges(
        component,
        feature["target_body"],
        feature["selection"],
        "chamfer",
    )
    chamfers = component.features.chamferFeatures
    chamfer_input = chamfers.createInput2()
    added = chamfer_input.chamferEdgeSets.addEqualDistanceChamferEdgeSet(
        edges,
        adsk.core.ValueInput.createByString(feature["distance"]),
        True,
    )
    if not added:
        raise RuntimeError("Fusion rejected the equal-distance chamfer edge set")
    built = chamfers.add(chamfer_input)
    built.name = feature.get("name", feature["id"])
    return built, edges.count


def _feature_by_name(component, name):
    feature = component.features.itemByName(name)
    if not feature:
        raise ValueError(f"Target feature not found: {name}")
    return feature


def _construction_axis(component, axis):
    return {
        "x": component.xConstructionAxis,
        "y": component.yConstructionAxis,
        "z": component.zConstructionAxis,
    }[axis]


def _pattern_entities(component, target_feature):
    entities = adsk.core.ObjectCollection.create()
    entities.add(_feature_by_name(component, target_feature))
    return entities


def _rectangular_pattern(component, feature):
    patterns = component.features.rectangularPatternFeatures
    pattern_input = patterns.createInput(
        _pattern_entities(component, feature["target_feature"]),
        _construction_axis(component, feature["direction_one"]),
        adsk.core.ValueInput.createByReal(feature["quantity_one"]),
        adsk.core.ValueInput.createByString(feature["spacing_one"]),
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
    )
    if "direction_two" in feature:
        added = pattern_input.setDirectionTwo(
            _construction_axis(component, feature["direction_two"]),
            adsk.core.ValueInput.createByReal(feature["quantity_two"]),
            adsk.core.ValueInput.createByString(feature["spacing_two"]),
        )
        if not added:
            raise RuntimeError("Fusion rejected rectangular pattern direction two")
    built = patterns.add(pattern_input)
    built.name = feature.get("name", feature["id"])
    return built


def _circular_pattern(component, feature):
    patterns = component.features.circularPatternFeatures
    pattern_input = patterns.createInput(
        _pattern_entities(component, feature["target_feature"]),
        _construction_axis(component, feature["axis"]),
    )
    pattern_input.quantity = adsk.core.ValueInput.createByReal(feature["quantity"])
    pattern_input.totalAngle = adsk.core.ValueInput.createByString(feature["total_angle"])
    pattern_input.isSymmetric = feature.get("symmetric", False)
    built = patterns.add(pattern_input)
    built.name = feature.get("name", feature["id"])
    return built


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


def _hole_pattern(component, design, feature, display_name):
    plane_name = feature.get("plane", "xy")
    sketch = _sketch_on_plane(
        component,
        design,
        feature.get("offset", "0 cm"),
        display_name,
        plane_name,
    )
    style = feature.get("style", "simple")
    if style == "simple":
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
        return _extrude(
            component,
            regions,
            feature["depth"] if not through_all else "1 cm",
            "cut",
            display_name,
            feature.get("direction", "positive"),
            through_all,
        )

    hole_diameter = _evaluate(design, feature["diameter"], positive=True)
    hole_features = component.features.holeFeatures
    hole_diameter_input = adsk.core.ValueInput.createByString(feature["diameter"])
    if style == "counterbore":
        outer_diameter = _evaluate(design, feature["counterbore_diameter"], positive=True)
        _evaluate(design, feature["counterbore_depth"], positive=True)
        if outer_diameter <= hole_diameter:
            raise ValueError("counterbore_diameter must be greater than diameter")
        hole_input = hole_features.createCounterboreInput(
            hole_diameter_input,
            adsk.core.ValueInput.createByString(feature["counterbore_diameter"]),
            adsk.core.ValueInput.createByString(feature["counterbore_depth"]),
        )
    elif style == "countersink":
        outer_diameter = _evaluate(design, feature["countersink_diameter"], positive=True)
        if outer_diameter <= hole_diameter:
            raise ValueError("countersink_diameter must be greater than diameter")
        angle_expression = feature["countersink_angle"]
        units = design.unitsManager
        if not units.isValidExpression(angle_expression, "deg"):
            raise ValueError(f"Invalid countersink angle expression: {angle_expression}")
        angle = units.evaluateExpression(angle_expression, "rad")
        if angle <= 0 or angle >= math.pi:
            raise ValueError("countersink_angle must be greater than 0 deg and less than 180 deg")
        hole_input = hole_features.createCountersinkInput(
            hole_diameter_input,
            adsk.core.ValueInput.createByString(feature["countersink_diameter"]),
            adsk.core.ValueInput.createByString(angle_expression),
        )
    else:
        raise ValueError(f"Unsupported hole style: {style}")
    if not hole_input:
        raise RuntimeError(f"Fusion failed to create a {style} hole input")

    sketch_points = adsk.core.ObjectCollection.create()
    for point in feature["points"]:
        sketch_points.add(sketch.sketchPoints.add(_point2(design, point)))
    if not hole_input.setPositionBySketchPoints(sketch_points):
        raise RuntimeError(f"Fusion rejected the {style} hole positions")

    direction = feature.get("direction", "positive")
    if feature["depth"] == "through_all":
        if not hole_input.setAllExtent(_direction(direction)):
            raise RuntimeError(f"Fusion rejected the {style} through-all extent")
    else:
        _evaluate(design, feature["depth"], positive=True)
        if not hole_input.setDistanceExtent(adsk.core.ValueInput.createByString(feature["depth"])):
            raise RuntimeError(f"Fusion rejected the {style} distance extent")
        # A sketch-point hole's natural direction is opposite the sketch normal.
        hole_input.isDefaultDirection = direction == "negative"
    built = hole_features.add(hole_input)
    built.name = display_name
    return built


def _apply_feature(component, design, feature):
    feature_id = feature["id"]
    display_name = feature.get("name", feature_id)
    feature_type = feature["type"]
    if feature_type in {"edge_fillet", "edge_chamfer"}:
        operation = "modify"
    elif feature_type in {"rectangular_pattern", "circular_pattern"}:
        operation = "pattern"
    else:
        operation = feature.get("operation", "cut" if feature_type == "hole_pattern" else "new_body")
    selected_edge_count = None
    generated_body_count = None

    if feature_type == "box":
        origin = feature.get("origin", ["0 cm", "0 cm", "0 cm"])
        profile = {
            "shape": "rectangle",
            "origin": origin[:2],
            "width": feature["width"],
            "height": feature["depth"],
        }
        sketch = _sketch_on_plane(component, design, origin[2], display_name)
        region = _draw_profile(sketch, design, profile)
        built = _extrude(component, region, feature["height"], operation, display_name)
    elif feature_type == "cylinder":
        center = feature.get("center", ["0 cm", "0 cm", "0 cm"])
        axis = feature.get("axis", "z")
        direction = feature.get("direction", "positive")
        if axis == "z":
            profile = {"shape": "circle", "center": center[:2], "diameter": feature["diameter"]}
            sketch = _sketch_on_plane(component, design, center[2], display_name)
            region = _draw_profile(sketch, design, profile)
        else:
            sketch, local_center, direction = _axis_sketch(
                component,
                design,
                center,
                axis,
                display_name,
                direction,
            )
            radius = _evaluate(design, feature["diameter"], positive=True) / 2
            sketch.sketchCurves.sketchCircles.addByCenterRadius(local_center, radius)
            if sketch.profiles.count != 1:
                raise ValueError(f"Cylinder must create exactly one closed region; got {sketch.profiles.count}")
            region = sketch.profiles.item(0)
        built = _extrude(component, region, feature["height"], operation, display_name, direction)
    elif feature_type == "sketch_extrude":
        sketch = _sketch_on_plane(
            component,
            design,
            feature.get("offset", "0 cm"),
            display_name,
            feature.get("plane", "xy"),
        )
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
        built = _hole_pattern(component, design, feature, display_name)
    elif feature_type == "mecanum_drive":
        built, generated_body_count = _mecanum_drive(component, design, feature)
    elif feature_type == "edge_fillet":
        built, selected_edge_count = _edge_fillet(component, design, feature)
    elif feature_type == "edge_chamfer":
        built, selected_edge_count = _edge_chamfer(component, feature)
    elif feature_type == "rectangular_pattern":
        built = _rectangular_pattern(component, feature)
    elif feature_type == "circular_pattern":
        built = _circular_pattern(component, feature)
    else:
        raise ValueError(f"Unsupported feature type: {feature_type}")

    if operation == "new_body" and feature_type != "mecanum_drive":
        for index in range(built.bodies.count):
            suffix = "" if built.bodies.count == 1 else f"_{index + 1}"
            built.bodies.item(index).name = f"{display_name}{suffix}"
    if feature_type == "mecanum_drive":
        appearance_name = "per-part appearances"
    else:
        no_appearance_types = {
            "edge_fillet",
            "edge_chamfer",
            "rectangular_pattern",
            "circular_pattern",
        }
        appearance_name = (
            None
            if feature_type in no_appearance_types
            else _apply_appearance(built, feature.get("appearance"))
        )
    return {
        "id": feature_id,
        "type": feature_type,
        "operation": operation,
        "name": display_name,
        "appearance_preset": feature.get("appearance"),
        "appearance_name": appearance_name,
        "selected_edge_count": selected_edge_count,
        "generated_body_count": generated_body_count,
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


def _result_checks(component, design, checks):
    if not checks:
        return None
    body_count = component.bRepBodies.count
    body_names = [component.bRepBodies.item(index).name for index in range(body_count)]
    expected_count = checks.get("body_count")
    if expected_count is not None and body_count != expected_count:
        raise ValueError(f"Result body_count is {body_count}; expected {expected_count}")

    missing = [name for name in checks.get("required_bodies", []) if name not in body_names]
    if missing:
        raise ValueError(f"Required result bodies are missing: {', '.join(missing)}")

    size = None
    if "minimum_size" in checks or "maximum_size" in checks:
        if body_count == 0:
            raise ValueError("Result size checks require at least one body")
        min_values = [math.inf, math.inf, math.inf]
        max_values = [-math.inf, -math.inf, -math.inf]
        for body_index in range(body_count):
            bounds = component.bRepBodies.item(body_index).boundingBox
            body_min = (bounds.minPoint.x, bounds.minPoint.y, bounds.minPoint.z)
            body_max = (bounds.maxPoint.x, bounds.maxPoint.y, bounds.maxPoint.z)
            for axis_index in range(3):
                min_values[axis_index] = min(min_values[axis_index], body_min[axis_index])
                max_values[axis_index] = max(max_values[axis_index], body_max[axis_index])
        size = [max_values[index] - min_values[index] for index in range(3)]
        axis_names = ("x", "y", "z")
        tolerance = 1e-6
        if "minimum_size" in checks:
            minimum = [_evaluate(design, value) for value in checks["minimum_size"]]
            for axis_index, required in enumerate(minimum):
                if required < 0:
                    raise ValueError("minimum_size values cannot be negative")
                if size[axis_index] + tolerance < required:
                    raise ValueError(
                        f"Result {axis_names[axis_index]} size is {size[axis_index]:.6g} cm; "
                        f"minimum is {required:.6g} cm"
                    )
        if "maximum_size" in checks:
            maximum = [_evaluate(design, value) for value in checks["maximum_size"]]
            for axis_index, allowed in enumerate(maximum):
                if allowed < 0:
                    raise ValueError("maximum_size values cannot be negative")
                if size[axis_index] - tolerance > allowed:
                    raise ValueError(
                        f"Result {axis_names[axis_index]} size is {size[axis_index]:.6g} cm; "
                        f"maximum is {allowed:.6g} cm"
                    )
    return {
        "passed": True,
        "body_count": body_count,
        "body_names": body_names,
        "size_cm": None if size is None else {"x": size[0], "y": size[1], "z": size[2]},
    }


def _summary_operation(feature):
    feature_type = feature["type"]
    if feature_type in {"edge_fillet", "edge_chamfer"}:
        return "modify"
    if feature_type in {"rectangular_pattern", "circular_pattern"}:
        return "pattern"
    return feature.get("operation", "cut" if feature_type == "hole_pattern" else "new_body")


def _summary(plan):
    return {
        "component_name": plan["component_name"],
        "parameter_count": len(plan.get("parameters", [])),
        "feature_count": len(plan["features"]),
        "features": [
            {
                "id": item["id"],
                "type": item["type"],
                "operation": _summary_operation(item),
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
        result_checks = _result_checks(component, design, plan.get("result_checks"))
        app.activeViewport.fit()
        return _result({
            "message": "Fusion model created",
            "plan_id": plan_id,
            "mode": mode,
            "document": app.activeDocument.name if app.activeDocument else None,
            "parameters": parameters,
            "built_features": built_features,
            "body_count": component.bRepBodies.count,
            "result_checks": result_checks,
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
        {
            "type": "string",
            "description": "A complete ModelPlan 1.0 or 1.1 JSON string",
            "maxLength": MAX_PLAN_BYTES,
        },
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
