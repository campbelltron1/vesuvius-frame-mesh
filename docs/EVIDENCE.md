# Evidence and reproducibility

Snapshot date: 2026-10-10. All selection logic is label-free. Labels appear
only in the separately identified w029 consequence score.

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

`SHA256SUMS` records every release-repository file except itself and `.git`.

## Claim limits

- The public corpus can change after the snapshot.
- The naming convention is treated as evidence only after canvas verification.
- A result of `resolved` means the public metadata identifies one same-frame
  mesh; it is not a physical accuracy guarantee.
- The AUC consequence is one segment/checkpoint and cannot support a general
  accuracy-lift claim.
