"""Read-only Fusion bridge status tool."""

import json

import adsk.core
import adsk.fusion

from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ..mcp_primitives.tool import Tool

app = adsk.core.Application.get()


def handler() -> dict:
    document = app.activeDocument
    design = adsk.fusion.Design.cast(app.activeProduct) if app.activeProduct else None
    payload = {
        "connected": True,
        "application": "Autodesk Fusion 360",
        "version": getattr(app, "version", None),
        "active_document": document.name if document else None,
        "active_product_is_design": bool(design),
        "server": "http://127.0.0.1:9100/",
        "supported_model_plan": "1.1",
        "supported_model_plan_versions": ["1.0", "1.1"],
        "modeling_capabilities": [
            "uploaded-sketch-to-model-plan",
            "multi-plane-sketches",
            "slots-and-compound-holes",
            "fillets-and-chamfers",
            "rectangular-and-circular-patterns",
            "mecanum-drive-assemblies",
            "post-build-result-checks",
        ],
    }
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": False,
        "message": "Fusion bridge is ready",
    }


tool = Tool.create_simple(
    name="fusion_status",
    description="Check whether the local Fusion AI modeling bridge is running and whether a Fusion design is active.",
).strict_schema()

register(Item.create_tool_item(tool=tool, handler=handler))
