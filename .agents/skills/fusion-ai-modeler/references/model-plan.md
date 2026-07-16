# ModelPlan 1.1

ModelPlan is the constrained JSON boundary between multimodal reasoning and the Fusion
executor. The machine-readable contract is `../assets/model-plan.schema.json`.

## Top-level fields

- `version`: use `1.1` for advanced features. Version `1.0` remains accepted for backward-compatible plans.
- `component_name`: name of the generated Fusion component.
- `units`: `mm`, `cm`, or `in`; physical expressions must still include units.
- `parameters`: optional Fusion user parameters with unique ASCII identifiers.
- `features`: ordered typed operations with unique IDs.
- `assumptions`: optional reversible visual or dimensional assumptions.
- `source_analysis`: optional structured provenance and orientation notes.
- `result_checks`: optional post-build assertions for body count, names, and overall size.

## Expressions

Write physical lengths as strings, for example `"20 mm"`, `"plate_width"`, or
`"hole_pitch / 2"`. Define parameters before features that reference them. Never emit a bare
JSON number for a physical length. Angles use a unit-bearing expression such as `"45 deg"`.

## Profiles and planes

`sketch_extrude.profile.shape` accepts:

- `rectangle`: `origin`, `width`, `height`;
- `circle`: `center`, `diameter`;
- `polyline`: three or more 2D `points`, closed by the executor;
- `rounded_rectangle`: `center`, `width`, `height`, `radius`; uses exact tangent lines/arcs;
- `annulus`: `center`, `outer_diameter`, `inner_diameter`; selects the ring region;
- `slot`: `center`, overall `length`, `width`, and optional `angle`; creates a center-to-center slot.

Profiles use coordinates local to the selected base plane:

| Plane | Profile coordinates | Offset axis |
| --- | --- | --- |
| `xy` | `[X, Y]` | Z |
| `xz` | `[X, Z]` | Y |
| `yz` | `[Y, Z]` | X |

Use `offset` along the plane's offset axis, `distance` for the extrusion length, and `direction`
as `positive` or `negative` relative to the selected plane.

## Features

### `box`

Requires `width`, `depth`, and `height`. Optional `origin` is `[x, y, z]`. Extrudes along +Z.

### `cylinder`

Requires `diameter` and `height`. Optional `center` is `[x, y, z]`. Optional `axis` is `x`, `y`,
or `z`; default is `z`.

### `sketch_extrude`

Requires one supported `profile` and `distance`. Optional: `plane` (`xy`, `xz`, or `yz`), `offset`,
`direction`, `operation`, and `appearance`.

### `hole_pattern`

Requires `diameter`, one or more plane-local 2D `points`, and `depth`. Depth is a length expression
or `through_all`. Operation must be `cut`. Optional `style` values:

- `simple` (default): cylindrical hole;
- `counterbore`: also requires `counterbore_diameter` and `counterbore_depth`;
- `countersink`: also requires `countersink_diameter` and `countersink_angle`.

The plane may be `xy`, `xz`, or `yz`; `offset` and `direction` follow the same convention as
`sketch_extrude`.

### `edge_fillet`

Modifies an earlier, uniquely named `new_body`. Requires `target_body`, `radius`, and one of:

- `top_perimeter`;
- `bottom_perimeter`;
- `top_and_bottom_perimeters`;
- `all`.

### `edge_chamfer`

Modifies an earlier, uniquely named `new_body`. Requires `target_body`, `distance`, and the same
typed `selection` values as `edge_fillet`.

### `rectangular_pattern`

Repeats an earlier named feature. Requires `target_feature`, `direction_one` (`x`, `y`, or `z`),
`quantity_one`, and `spacing_one`. Add `direction_two`, `quantity_two`, and `spacing_two` together
for a two-direction pattern.

### `circular_pattern`

Repeats an earlier named feature around `axis` (`x`, `y`, or `z`). Requires `target_feature`,
`quantity`, and `total_angle`; optional `symmetric` is a Boolean.

### `mecanum_drive`

Creates a bounded, detailed drive module inside one named BaseFeature. Required top-level fields
are `center`, `side` (`left` or `right`), `handedness` (`A` or `B`), and these complete sections:

- `wheel`: envelope, roller count/taper/axis, hub, flange, socket, and spoke dimensions;
- `coupler`: hex, barrel, bore, set-screw, retention-thread, and shaft engagement dimensions;
- `motor`: shaft, boss, gearbox, motor can, encoder, and mount-circle dimensions;
- `bracket`: sheet, base, wall, axis height, U-clearance, keyholes, and base-hole grid;
- `hardware`: motor, base, and wheel-retention fastener envelopes;
- `appearances`: typed presets for hub, roller, axle, coupler, motor, encoder, bracket, and hardware.

See `../../../../examples/generic-mecanum-drive-module.modelplan.json` for the full field set. Confirm
the roller hand by its axis direction, not by an ambiguous seller-side label. The feature is
direct-editable but not live-parametric; regenerate it from the revised plan after dimension changes.

## Result checks

`result_checks` may contain:

- `body_count`: exact expected final body count;
- `required_bodies`: one or more exact final body names;
- `minimum_size`: `[X, Y, Z]` lower bounds for the component bounding size;
- `maximum_size`: `[X, Y, Z]` upper bounds for the component bounding size.

Size entries are unit-bearing expressions. A failed result check fails the build; in
`new_document` mode, the partially generated document is closed.

## Body and operation ordering

Create a `new_body` before `join`, `cut`, or `intersect`. Every `new_body` display name and every
feature name used as a pattern seed must be unique. Create a pattern seed before its pattern and
a body before its fillet or chamfer. Prefer additive bodies, then cuts, then edge treatments.

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
