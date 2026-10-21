# Evidence and reproducibility

Corpus snapshot date: 2026-10-10; consequence extension: 2026-10-12. All
selection logic is label-free. Labels appear only in the separately identified
downstream consequence evaluations.

## Corpus result

An independent implementation recomputed target volume ids, render scaling,
stored TIFF dimensions, and surface canvases from raw public metadata. It
resolved all 650 audited surface volumes uniquely, with zero disagreements
against the packaged resolver.

```text
total:                  650
resolved:               650
producer disagreements: 0
manifest sha256:         cedf131cb5c4293be101db84043db9c765ff08e9952c00673a8a5885db970c2b
```

The installed one-process/two-worker batch replay also selected the exact
independent mesh for 650/650 cases. It downloaded 6,322,009 metadata bytes in
5,833 requests under a 250 MiB gate. This is reliability evidence, not a
concurrency speed claim.

## Disjoint corpus extension

A separately frozen 2026-10-14 run covers 42 additional surface-volume
identities from PHerc0009B (36) and PHerc0841 (6). All 42 resolved uniquely and
matched expectations frozen before the completed output was checked. None of
the extension identities occurs in the 650-case primary audit, giving 692
tested selections across 11 sample names.

This is a compatibility extension, not a second independent implementation.
See [EXTENSION_RESULT.md](EXTENSION_RESULT.md) for exact hashes, checks, and
claim limits.

## One external accuracy consequence

For PHerc0139 segment `20250108000004-w029_2025010827` and surface volume
`2.399um-0.22m-78keV-volume-20260102150214.zarr`:

| arm | AUC |
|---|---:|
| coarse-frame reproduction | 0.86069 |
| resolver-selected target-frame reproduction | 0.89615 |
| difference | +0.03546 |

The model, labels, benchmark construction, and original mesh-choice finding
come from Villa issue #1845. This repository's claim is the metadata-only
selection/abstention mechanism and its corpus validation.

## Preregistered eight-case consequence

The extension uses all eight pre-existing labelled PHerc0139 C2 windows. Both
arms use the same 2.399 um target CT, local renderer, canonical 2 um ink model,
canvas window, and native-selected evaluation offset. Only the mesh source
changes: native 2.399 um versus 9.362 um mapped into the target frame.

| endpoint | result |
|---|---:|
| mean fixed-canvas AUC delta | +0.01845 |
| median fixed-canvas AUC delta | +0.01778 |
| positive cases | 7/8 |
| segment-bootstrap 95% interval | [+0.00823, +0.02872] |
| exact one-sided sign-test p | 0.03516 |
| exact one-sided mean sign-flip p | 0.01172 |
| locked sensitivity configurations retaining GO | 36/36 |

When the wrong-frame arm receives its own label-selected alignment, native
minus wrong-frame AUC averages only +0.00173 and is positive in 3/8 cases.
This bounds the claim: native-frame selection primarily prevents spatial
misplacement on a shared canvas. The independently selected offset difference
has median magnitude 0.104 mm (range 0.054–0.192 mm), but is an evaluation
alignment burden rather than physical registration ground truth.

The complete table, preregistration, robustness audit, case-level JSON, hashes,
and explicitly outcome-selected explanatory figure are in `docs/` and
`evidence/multicase/`.

## Reproduce the offline corpus verification

```bash
PYTHONPATH=src python src/verify_volume_frame_corpus.py \
  evidence/corpus/F_canvas_provenance_pherc0139.json \
  evidence/corpus/F_canvas_provenance_validation.json \
  evidence/corpus/F_canvas_provenance_controls.json \
  --output independent-verification.json
cmp evidence/results/J1_volume_frame_independent_verification.json \
  independent-verification.json
```

## Machine-readable evidence

- `evidence/corpus/F_canvas_provenance_pherc0139.json`
- `evidence/corpus/F_canvas_provenance_validation.json`
- `evidence/corpus/F_canvas_provenance_controls.json`
- `evidence/results/J1_volume_frame_independent_verification.json`
- `evidence/results/N3_installed_native_full_corpus_replay.json`
- `evidence/results/K4_corpus_drift_result.json`
- `evidence/results/G_volume_frame_mesh_20261010.json`
- `evidence/extension/HP7_extension_expectations.json`
- `evidence/extension/HP7_extension_result.json`
- `evidence/multicase/frame_resolver_multicase.json`
- `evidence/multicase/frame_resolver_sensitivity.json`
- `evidence/multicase/frame_resolver_displacement.json`
- `evidence/multicase/cases/*.json`

`SHA256SUMS` records every release-repository file except itself and `.git`.

## Claim limits

- The public corpus can change after the snapshot.
- The 42-case extension verifies frozen completed outputs and is not a fresh
  live-network replay.
- The naming convention is treated as evidence only after canvas verification.
- A result of `resolved` means the public metadata identifies one same-frame
  mesh; it is not a physical accuracy guarantee.
- The eight-case result is specific to its fixed PHerc0139 windows, target CT,
  checkpoint, renderer, and labels; it is not a general model-quality claim.
- The independently realigned diagnostic is nearly neutral on average, so the
  demonstrated benefit is chiefly correct placement on a shared canvas.
