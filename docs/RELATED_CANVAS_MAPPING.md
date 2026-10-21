# Relationship to the public TIFFXYZ canvas-map work

The public
[`tifxyz-canvas-map`](https://github.com/slchicoineau-creator/tifxyz-canvas-map)
package by Sebastien Chicoineau (`@slchicoineau-creator`) and Chris Thomas
(`@Baz248`) addresses the next step after a mesh pair is known: it derives the
exact fine/coarse TIFF pixel-coordinate mapping and validates that mapping on
public data.

This repository addresses an earlier decision. Given one target surface volume,
it discovers published mesh directories, chooses a unique mesh in the target
volume's frame only after canvas verification, and otherwise abstains. Its
batch command adds bounded concurrency, checkpointing, and deterministic
failure reporting.

The projects are complementary:

1. `vesuvius-frame-mesh` identifies the mesh whose frame matches the requested
   surface volume.
2. `tifxyz-canvas-map` maps pixels between a fine/coarse pair that has already
   been selected.

No canvas-map source code or CC BY-NC evidence table is bundled here. The
coordinate formula, 207-pair table, and physical residual measurements belong
to that project and are not claims of this repository.
