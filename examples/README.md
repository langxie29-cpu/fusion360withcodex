# ModelPlan examples

These examples are original, generic mechanical designs with no brand geometry, logos, or
copied product imagery. They are intended to demonstrate editable feature stacks rather than
manufacturing-ready specifications.

| Example | Scenario | Detail highlights |
| --- | --- | --- |
| `generic-sensor-enclosure.modelplan.json` | Sealed desktop/industrial sensor housing | Hollow shell, removable lid, fasteners, vents, optical window, status light, button |
| `robotics-control-panel.modelplan.json` | Compact robot or lab controller faceplate | Display cutout/glass, rotary control, push control, indicators, rear standoffs |
| `configurable-stepper-mount.modelplan.json` | Adjustable generic stepper-motor adapter | Parametric pitch, central clearance, locating ring, annular spacers, ribs, isolation pads |
| `generic-mecanum-drive-module.modelplan.json` | Brand-neutral robot drive module | Nine angled barrel rollers, hub/flanges/spokes, coupler, geared motor, bracket, fasteners, result assertions |

All dimensions are design examples. Motor pitch, fastener clearance, sealing, wall thickness,
material, tolerances, ingress protection, and load capacity must be verified for the actual part
and manufacturing process.

The mecanum example is intentionally a single reusable module. Instantiate four modules with the
required `side` and A/B `handedness`, then visually confirm the intended roller-axis pattern. Its
purchased-part dimensions are integration candidates, not vendor-certified values.

Validate all examples:

```powershell
Get-ChildItem examples/*.modelplan.json | ForEach-Object {
  python .agents/skills/fusion-ai-modeler/scripts/validate_model_plan.py $_.FullName
}
```
