"""
Tools package for Fusion MCP Add-in

This package provides tools for the Fusion MCP Add-in.
"""

# Register only bounded, typed tools. Arbitrary API-script execution is deliberately excluded.
from . import fusion_status
from . import inspect_design
from . import model_plan
from . import get_screenshot
