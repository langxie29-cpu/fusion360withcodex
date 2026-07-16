---
name: fusion-ai-modeler
description: Turn an uploaded hand sketch, dimensioned drawing, reference image, or natural-language mechanical requirement into a validated, editable Fusion 360 model through the local fusion360 MCP bridge. Use for sketch-to-CAD, detailed multi-plane parts, brackets, plates, enclosures, motor mounts, bearing interfaces, patterned features, compound holes, mecanum drive assemblies, and other parametric mechanical work; also use to inspect or revise a model already open in Fusion 360.
---

# Fusion AI Modeler

Convert what is visible in an image uploaded to GPT into an explicit `ModelPlan`, validate it, execute only typed Fusion tools, and verify the resulting geometry from screenshots and result assertions.

## Non-negotiable rules

- Inspect the image uploaded in the chat with the current multimodal GPT model. Do not run a local OCR, OpenCV, edge-detection, or image-vectorization pipeline.
- Separate visible facts from inferred geometry. Never infer real-world scale from pixels alone.
- Never invent a vendor-part dimension. Use the bundled catalog only as a candidate interface and label generic values as unverified until an exact manufacturer part number or datasheet is available.
- Default to `mode="new_document"`. Modify `active_document` only when the user explicitly asks to change the open design.
- Use `validate_and_stage_model_plan` followed by `apply_staged_model_plan`; never add or expose an arbitrary code-execution tool.
- Stage and review every plan before a write, then inspect screenshots after every write.

## Workflow

### 1. Check the Fusion bridge

Call `fusion_status` first. If the bridge is unavailable, ask the user to open Fusion 360 and run the `FusionAIModeler` add-in from **Utilities > Scripts and Add-Ins > Add-Ins**. Continue planning while Fusion is unavailable.

### 2. Read the uploaded sketch

Read [sketch-analysis.md](references/sketch-analysis.md) before interpreting an image.

Summarize:

- detected views and their likely correspondence;
- visible dimensions, units, hole counts, symmetry, and feature relationships;
- uncertain text or geometry with confidence labels;
- missing dimensions that affect topology, fit, or scale.

Ask only for ambiguities that would materially change topology or fit. For cosmetic details, state a reversible assumption and continue. If there is no scale reference or dimension, request one real dimension before building.

For high-detail work, inspect close crops or additional views when the first image does not resolve
fillet radii, hole style, slot endpoints, edge breaks, mating faces, or repeated-feature spacing.
Do not treat visual resemblance as dimensional confirmation.

### 3. Resolve standard-part interfaces

For motors, bearings, fasteners, shafts, fans, or other purchased parts, run:

```powershell
python .agents/skills/fusion-ai-modeler/scripts/search_parts.py "<part name or number>"
```

Read [parts-policy.md](references/parts-policy.md). Prefer an exact manufacturer part number and primary datasheet. If only a generic family is known, create a configurable interface and expose its uncertain dimensions as user parameters.

### 4. Produce a ModelPlan

Read [model-plan.md](references/model-plan.md) and start from [model-plan.example.json](assets/model-plan.example.json). Keep every physical length as a unit-bearing string or parameter expression, such as `"42 mm"` or `"plate_width / 2"`.

Save the candidate plan to a JSON file and validate it locally:

```powershell
python .agents/skills/fusion-ai-modeler/scripts/validate_model_plan.py <plan.json>
```

Use ModelPlan `1.1` for multi-plane geometry, slots, compound holes, chamfers, patterns,
result checks, or `mecanum_drive`; retain `1.0` only for backward-compatible simple plans.
Fix every validation error before calling Fusion.

### 5. Stage and build

Call `validate_and_stage_model_plan` with the validated JSON string. This runs off Fusion's main thread, changes no document, and returns a short `plan_id`. Review the returned feature summary. Then call `apply_staged_model_plan` with that ID and the intended mode.

Use `active_document` only after taking a `get_screenshot` and `inspect_design`. Treat a failed feature as a plan error; do not silently skip it.

### 6. Verify visually

Capture at least `iso-top-right`, `top`, and the most informative orthographic side. Compare:

- overall proportions and orientation;
- feature count, location, and symmetry;
- through-holes versus blind cuts;
- interfaces against confirmed part dimensions.
- required body names, body count, and size bounds when `result_checks` are present.

If the result is wrong, revise the plan and build a fresh document unless the user asked for in-place editing. End with a concise list of assumptions and unverified dimensions.

## Supported ModelPlan features

- `box`: rectangular prism on an XY offset plane.
- `cylinder`: circular extrusion along X, Y, or Z.
- `sketch_extrude`: rectangle, circle, closed polyline, exact rounded rectangle, annulus, or angled slot on XY, XZ, or YZ.
- `hole_pattern`: simple, counterbored, or countersunk holes on XY, XZ, or YZ; blind or through-all.
- `edge_fillet` and `edge_chamfer`: typed edge treatments on a named body.
- `rectangular_pattern` and `circular_pattern`: repeat an earlier named feature along typed directions or axes.
- `mecanum_drive`: generate one detailed, bounded wheel/roller, coupler, geared-motor, bracket, and fastener assembly with A/B roller handedness.
- `result_checks`: assert final body count, required body names, and minimum or maximum overall size.

Prefer a native compound hole over stacked circular cuts, one seed feature plus a typed pattern
over repeated copied profiles, and an explicit slot over a near-capsule rounded rectangle. Use
fillets and chamfers only after the target body exists. Name every body and pattern seed uniquely.

For mecanum chassis work, use one `mecanum_drive` per corner, pair A/B hands to form the intended
roller-axis pattern, and inspect front, rear, top, and underside views. Treat all wheel, shaft,
coupler, bracket, and fastener values as purchased-part interfaces requiring measurement or a
primary datasheet. The generated arbitrary-axis bodies live in a named BaseFeature: revise the
ModelPlan and regenerate after changing their dimensions.

Any feature can optionally use a typed `appearance` preset: `silver_aluminum`, `silver_glass`,
`silver_metal`, `black_glass`, `black`, or `flash_white`. Presets resolve only through Fusion's
built-in Appearance Library; arbitrary material code or paths are not accepted.

Use `new_body`, `join`, `cut`, or `intersect` where the feature allows it. Prefer a simple sequence of additive bodies followed by cuts.

## Reference routing

- Read [sketch-analysis.md](references/sketch-analysis.md) for every uploaded sketch or drawing.
- Read [model-plan.md](references/model-plan.md) before generating or repairing plan JSON.
- Read [parts-policy.md](references/parts-policy.md) whenever purchased components or standard interfaces appear.
