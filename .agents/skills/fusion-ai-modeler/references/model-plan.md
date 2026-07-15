# ModelPlan 1.0

ModelPlan is the constrained JSON boundary between multimodal reasoning and the Fusion
executor. The machine-readable contract is `../assets/model-plan.schema.json`.

## Top-level fields

- `version`: always `1.0`.
- `component_name`: name of the generated Fusion component.
- `units`: `mm`, `cm`, or `in`; physical expressions must still include units.
- `parameters`: optional Fusion user parameters with unique ASCII identifiers.
- `features`: ordered typed operations with unique IDs.
- `assumptions`: optional reversible visual or dimensional assumptions.
- `source_analysis`: optional structured provenance and orientation notes.

## Expressions

Write physical lengths as strings, for example `"20 mm"`, `"plate_width"`, or
`"hole_pitch / 2"`. Define parameters before features that reference them. Never emit a bare
JSON number for a physical length.

## Profiles

`sketch_extrude.profile.shape` accepts:

- `rectangle`: `origin`, `width`, `height`;
- `circle`: `center`, `diameter`;
- `polyline`: three or more 2D `points`, closed by the executor;
- `rounded_rectangle`: `center`, `width`, `height`, `radius`; uses exact tangent lines/arcs;
- `annulus`: `center`, `outer_diameter`, `inner_diameter`; selects the ring region.

The current protocol supports only XY sketches. Use `offset` to select the sketch elevation,
`distance` for the extrusion length, and `direction` as `positive` or `negative`.

## Features

### `box`

Requires `width`, `depth`, and `height`. Optional `origin` is `[x, y, z]`. Extrudes along +Z.

### `cylinder`

Requires `diameter` and `height`. Optional `center` is `[x, y, z]`. Extrudes along +Z.

### `sketch_extrude`

Requires one supported `profile` and `distance`. Optional: `plane` (`xy`), `offset`,
`direction`, `operation`, and `appearance`.

### `hole_pattern`

Requires `diameter`, one or more 2D `points`, and `depth`. Depth is a length expression or
`through_all`. Operation must be `cut`.

### `edge_fillet`

Modifies an earlier, uniquely named `new_body`. Requires `target_body`, `radius`, and one of:

- `top_perimeter`;
- `bottom_perimeter`;
- `top_and_bottom_perimeters`;
- `all`.

## Body and operation ordering

Create a `new_body` before `join`, `cut`, or `intersect`. Every `new_body` display name must be
unique because later modifiers address bodies by name. Prefer additive bodies followed by cuts.

## Appearance presets

Features may use only these presets:

- `silver_aluminum`
- `silver_glass`
- `silver_metal`
- `black_glass`
- `black`
- `flash_white`

The executor resolves them only from Fusion's built-in Appearance Library. Arbitrary material
code, external file paths, and scripts are not accepted.
