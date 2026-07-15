# FusionAIModeler Add-In

Local, dependency-free MCP server that runs inside Autodesk Fusion and binds to
`127.0.0.1:9100`.

Registered tools:

- `fusion_status`
- `inspect_design`
- `validate_and_stage_model_plan`
- `apply_staged_model_plan`
- `get_screenshot`

The add-in exposes no arbitrary Python or Fusion script execution. Geometry writes must pass
the ModelPlan validator, receive an expiring one-time plan ID, and then be applied through the
typed executor on Fusion's main thread.

Browser-originated writes are blocked with loopback Host/Origin checks and an
`application/json` requirement. See the repository `SECURITY.md` for the complete threat model.

Parts of the HTTP server, task bridge, MCP primitives, and screenshot foundation are derived
from Autodesk's `FusionMCPSample`. The retained upstream MIT license is in
`LICENSE.autodesk.txt`.
