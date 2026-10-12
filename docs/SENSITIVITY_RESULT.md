# Frame-resolver eight-case sensitivity audit — result

## Decision

**ROBUST.** All 36 locked sensitivity configurations retained the original GO
decision. Every configuration remained 7/8 positive, and mean paired AUC gain
ranged from **+0.01422 to +0.01937**. The median configuration mean was
**+0.01841**.

This strengthens the conclusion that choosing the CT-native mesh prevents a
measurable fixed-canvas ink-evaluation loss. It does not change the more
limited independently-realigned result: the principal benefit remains spatial
placement, not a demonstrated intrinsic improvement to ink features after a
label-assisted registration.

## Frozen sensitivity families

The audit used the eight existing prediction pairs without rerendering or
reinference. It evaluated:

- nine native-alignment searches: radii 16, 32, and 48 crossed with grid steps
  1, 2, and 4 downsampled pixels;
- 27 evaluation masks: positive thresholds 0.4, 0.5, and 0.6 crossed with
  negative thresholds 0.01, 0.05, and 0.10 and near-ink bands of 1.5, 2.0, and
  2.5 mm.

The native-selected offset was reused for both arms in every configuration.
All eight native and wrong-frame prediction hashes matched the primary case
records before scoring.

## Range and worst cases

| configuration | mean delta | median delta | positive cases | worst case | decision |
|---|---:|---:|---:|---:|---|
| radius 16/32/48, step 4 | +0.01422 | +0.01308 | 7/8 | -0.00883 | GO |
| canonical radius 48, step 2 | +0.01845 | +0.01778 | 7/8 | -0.00488 | GO |
| positive 0.40, negative 0.10, band 2.5 mm | +0.01937 | +0.01786 | 7/8 | -0.00127 | GO |

The three step-4 rows tie for the weakest mean. They still clear the +0.010
mean gate and retain 7/8 positive cases. No configuration reversed the mean or
dropped below seven positive cases.

## Exact small-sample checks

Using the eight canonical paired deltas:

- one-sided exact sign-test probability: **0.03515625**;
- one-sided exact paired mean sign-flip probability: **0.01171875** over all
  256 sign assignments.

These checks describe this fixed eight-window set. They do not establish that
the windows are independent random samples of all scroll data.

## Reproduction and resources

Preregistration: `bf1951d`
([`SENSITIVITY_PREREGISTRATION.md`](../evidence/multicase/SENSITIVITY_PREREGISTRATION.md)).
Executable code: `2920fdf001e6434c7c79f996ee01bb81e2e0e936`.

```bash
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 \
  PYTHONPATH=src .venv/bin/python \
  src/audit_frame_multicase_sensitivity.py \
  --output results/frame_resolver_sensitivity.json
```

The provenance-correct replay took 163.8 seconds. Maximum resident set size was
1,042,972,672 bytes (about 0.97 GiB); macOS reported a 1.96 GiB peak memory
footprint. The result JSON is
[`frame_resolver_sensitivity.json`](../evidence/multicase/frame_resolver_sensitivity.json).
