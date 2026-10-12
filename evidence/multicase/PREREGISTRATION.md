# Frame-resolver multi-case consequence extension — preregistration

Frozen: 2026-10-12 Australia/Sydney, before any new arm was rendered or scored.

## Question

Does selecting the mesh made in the requested CT frame improve ink-model AUC
across all eight public PHerc0139 segments with human ink labels, rather than
only the w029 example from Villa issue #1845?

This experiment strengthens or limits the existing frame-resolver claim. It is
not a new model, registration method, or transform-quality claim.

## Fixed cases

The complete pre-existing C2 human-label set is used; no segment may be added,
removed, or replaced after scoring:

1. `20250108000005-w030_2025010818`
2. `20250831000000-w040_2025083102`
3. `20260108000000-w041_2026010816`
4. `20260112000000-w043_2026011217`
5. `20260115000000-w044_2026011522`
6. `20260126000000-w045_2026012619`
7. `20260302000000-w039_2026030210`
8. `20260317000000-w035_2026031718`

The evaluation windows are the already-frozen `window_cells` values in
`results/C2_march_scan_8_segments.json`. They were selected as the densest
labelled windows before this experiment and are not reselected here.

## Fixed arms

Both arms render the same canvas window from target CT volume
`20260102150214-2.399um-0.2m-78keV-masked.zarr` with the same local renderer,
layer count, target sampling, normal interpolation and canonical 2 µm ink
checkpoint.

- **Native:** `<segment>-on-20260102150214-2.399um.tifxyz`.
- **Wrong frame:** `<segment>-on-20250728140407-9.362um.tifxyz`, carried into
  the target frame by `official_9to2.json` at immutable scroll-lineup revision
  `fb35a11d7d469beb131256c041267a99397e55f7`.

The wrong-frame arm deliberately reproduces the avoidable choice that the
metadata-only resolver prevents. No CT intensity, ink prediction, or label is
used to select either mesh or transform.

## Fixed evaluation

- Model: `scrollprize/ink_canonical_2um`, checkpoint
  `r152_3ddec_v2_l5_epoch13.ckpt`.
- Primary alignment: choose the offset using only the native prediction and
  reuse that identical offset and label mask for the wrong-frame prediction.
- Secondary conservative diagnostic: allow the wrong-frame prediction its own
  best label alignment. This is reported but is not the primary endpoint.
- Per case: report native AUC, wrong-frame AUC, delta, both offsets, labelled
  positive/negative counts and artifact hashes.
- Aggregate: report the mean and median paired delta, number of positive
  cases, worst case, and a deterministic segment-bootstrap 95% interval.

The labels are evaluation-only. They may choose the native evaluation offset
under the frozen scorer but may not alter the mesh, affine, renderer, model,
depth window, or case inclusion.

## Gates

- **GO:** mean paired AUC gain at least +0.010 and at least 6/8 segments are
  positive.
- **INCONCLUSIVE:** mean gain from +0.005 through +0.010, or fewer than 6/8
  positive, with no pipeline failure.
- **NO-GO:** mean gain below +0.005 or any discovered methodological defect
  that prevents a fair paired comparison.

All eight valid cases must be reported. The result cannot be described as an
accuracy improvement if only a selected subset passes.

## Resource and stop rules

- Mac only; no AWS or paid compute.
- One render or inference process at a time.
- Two numeric threads, two render workers, inference batch one, no compilation.
- MPS allocation fraction at most 0.25 and a 35% free-memory gate before each
  heavy stage.
- Require at least 12 GiB free before every heavy stage. Each case uses its own
  disposable CT-chunk cache, and raw render stacks are removed only after both
  predictions have been scored successfully.
- Stream only required public CT chunks; never download the full volume.
- Stop rather than compete with interactive work if the memory gate fails.
- Engineering pilot is the first lexicographic case, w030. Pilot output cannot
  change the fixed case set, arms, evaluator, or thresholds.
