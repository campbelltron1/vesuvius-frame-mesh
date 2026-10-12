# v0.1.2 — initial public release

This release provides a fail-closed, metadata-only resolver for selecting the
published `tifxyz` mesh built in a requested Vesuvius surface volume's CT
frame.

Highlights:

- single-case and bounded one/two-worker batch CLIs;
- explicit `resolved`, `missing`, `ambiguous`, and `invalid_metadata` results;
- canvas verification against both `.zattrs` and the stored level-0 Zarr
  shape;
- atomic metadata caching and plan-bound checkpoint/resume;
- 650-case frozen public-corpus evidence plus an independent verifier; and
- deterministic wheel and normalized-sdist builds.

The clean release repository passes 111 tests. The supplied release verifier
builds twice, installs the wheel into a temporary environment, exercises both
commands, runs the complete clean suite, and exact-compares the 650-case
replay. Artifact sizes and SHA-256 digests are recorded in
`release_verification.json`, repository `SHA256SUMS`, and
`dist/SHA256SUMS` for the uploadable release assets.

Claim limit: the +0.03546 w029 AUC consequence is one previously defined
benchmark, not a new multi-segment accuracy result. The tool verifies frame
and canvas provenance; it does not improve mesh geometry, transforms, depth
seating, or the ink model.
