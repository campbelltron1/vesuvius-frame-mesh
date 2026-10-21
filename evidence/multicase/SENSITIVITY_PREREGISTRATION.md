# Frame-resolver eight-case sensitivity audit — preregistration

Frozen 2026-10-12 Australia/Sydney, after the primary eight-case result and
before calculating any sensitivity variant.

## Purpose

The primary experiment returned a mean fixed-canvas AUC gain of +0.01845 in
7/8 cases. This audit asks whether that conclusion depends on the scorer's
alignment search grid or reasonable label-mask conventions. It does not tune
the resolver, meshes, affine, renders, predictions, windows, or case set.

## Frozen inputs

- The eight case records and compact prediction maps in
  `results/frame_resolver_multicase_cases/` and
  `src/out/frame_resolver_multicase/`.
- The case order and windows in `results/C2_march_scan_8_segments.json`.
- The public label arrays already cached under `src/out/march_bench/`.
- Native minus wrong-frame AUC remains the direction of every comparison.
- The alignment selected from the native prediction is reused for both arms.

Prediction hashes must match the primary case records before any variant is
accepted. No render or inference will be repeated.

## Fixed sensitivity families

### Alignment search

Use every Cartesian combination of:

- search radius: 16, 32, or 48 downsampled pixels;
- grid step: 1, 2, or 4 downsampled pixels.

All other canonical mask settings stay fixed: positive threshold 0.5,
negative threshold 0.05, and a 2.0 mm near-ink band.

### Evaluation mask

Reuse the canonical native alignment (radius 48, step 2) and evaluate every
Cartesian combination of:

- positive threshold: 0.4, 0.5, or 0.6;
- negative threshold: 0.01, 0.05, or 0.10;
- near-ink band: 1.5, 2.0, or 2.5 mm.

The near-ink distance is always measured from the fixed 0.5 label contour, so
changing the positive threshold does not silently redefine the distance band.

## Statistics

For each configuration report mean and median paired AUC delta, positive-case
count, worst case, and the original GO decision (mean at least +0.010 and at
least 6/8 positive). For the canonical eight deltas also report:

- the exact one-sided sign-test probability;
- the exact one-sided paired sign-flip probability for the observed mean.

These are descriptive small-sample checks, not a claim that the eight windows
are independent draws from all possible scroll data.

## Locked interpretation gates

- **ROBUST:** at least 90% of all 36 configurations satisfy the original GO
  rule, every configuration has positive mean delta, and none has fewer than
  5/8 positive cases.
- **STABLE BUT THRESHOLD-SENSITIVE:** the median configuration satisfies GO,
  at least 75% satisfy GO, every mean is positive, and none has fewer than 5/8
  positive cases.
- **FRAGILE:** any configuration reverses the mean, any configuration has
  fewer than 5/8 positive cases, or fewer than 75% satisfy GO.

All configurations will be reported. The primary result remains the
preregistered endpoint; this audit can qualify its interpretation but cannot
replace it with a preferred variant.

## Resource limits

- Mac only, no AWS or paid service.
- One process, at most two numerical threads.
- Less than 4 GiB incremental memory and 2 GiB new disk.
- Stop on any prediction-hash mismatch or scientific-field mismatch when the
  canonical scorer is replayed.
