"""Read-only summary of the active Fusion design."""

import json
import traceback

import adsk.core
import adsk.fusion

from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ..mcp_primitives.tool import Tool

app = adsk.core.Application.get()


def handler() -> dict:
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if not design:
            raise RuntimeError("No active Fusion design")
        parameters = []
        for index in range(design.userParameters.count):
            parameter = design.userParameters.item(index)
            parameters.append({"name": parameter.name, "expression": parameter.expression, "unit": parameter.unit})
        components = []
        total_bodies = 0
        total_sketches = 0
        for component_index in range(design.allComponents.count):
            component = design.allComponents.item(component_index)
            bodies = []
            for index in range(component.bRepBodies.count):
                body = component.bRepBodies.item(index)
                bodies.append({
                    "name": body.name,
                    "visible": body.isVisible,
                    "solid": body.isSolid,
                    "face_count": body.faces.count,
                    "edge_count": body.edges.count,
                })
            features = []
            for index in range(component.features.count):
                feature = component.features.item(index)
                features.append({"name": feature.name, "type": feature.objectType})
            total_bodies += component.bRepBodies.count
            total_sketches += component.sketches.count
            components.append({
                "name": component.name,
                "body_count": component.bRepBodies.count,
                "sketch_count": component.sketches.count,
                "bodies": bodies,
                "features": features,
            })
        root = design.rootComponent
        payload = {
            "document": app.activeDocument.name if app.activeDocument else None,
            "root_component": root.name,
            "body_count": total_bodies,
            "sketch_count": total_sketches,
            "occurrence_count": root.allOccurrences.count,
            "parameters": parameters,
            "components": components,
        }
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
            "isError": False,
            "message": "Active design inspected",
        }
    except Exception as exc:
        app.log(f"Design inspection failed: {exc}\n{traceback.format_exc()}")
        return {
            "content": [{"type": "text", "text": str(exc)}],
            "isError": True,
            "message": "Design inspection failed",
        }


tool = Tool.create_simple(
    name="inspect_design",
    description="Read the active Fusion design's component, parameters, bodies, sketches, and feature summary before editing.",
).strict_schema()

register(Item.create_tool_item(tool=tool, handler=handler))
