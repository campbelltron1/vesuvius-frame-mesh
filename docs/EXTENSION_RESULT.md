# Disjoint 42-case corpus extension

This evidence-only extension asks whether the packaged resolver's frozen,
label-free selection rule continues to choose the preregistered mesh on public
surface volumes outside the primary 650-case audit.

The expectations and completed output were frozen on 2026-10-14. They cover
42 surface volumes from two samples absent from the primary audit:

| sample | cases | exact expected selections |
|---|---:|---:|
| PHerc0009B | 36 | 36 |
| PHerc0841 | 6 | 6 |
| **extension total** | **42** | **42** |

Every case resolved to exactly one canvas-compatible, volume-named mesh. All
42 selections matched the frozen expectations, none used labels, and none of
the 42 surface-volume identities occurs in the primary 650-case evidence.
The two sets therefore contain 692 tested selections across 11 sample names.

This is a compatibility extension, not a new independent implementation. The
primary claim remains the independently recomputed 650/650 audit. The extension
adds coverage of two more public samples and demonstrates that the same
fail-closed rule applied without a code or threshold change.

## Frozen artifacts

- `evidence/extension/HP7_extension_expectations.json`
  (`2e20f58e0bdf2be1f09cbca280cee65efdf18cff2977059b280cea97ceb50197`)
- `evidence/extension/HP7_extension_result.json`
  (`b9411f5e73db9e0501a3aa53a1c22241489196b7e502798d8c8fb7907818293a`)

The completed run used one process, two workers, a 64 MiB application budget,
and 403,428 downloaded metadata bytes. It did not download CT or mesh bodies.

## Reproduce the evidence checks

```bash
python scripts/verify_extension_evidence.py
```

The verifier checks the frozen file hashes, all 42 expected selections,
label-free status, unique resolution, the 36/6 sample split, zero identity
overlap with the primary corpus, and the 650 + 42 = 692 accounting.

## Claim limits

- This extension rechecks frozen completed outputs; it is not a fresh live
  network replay.
- It validates metadata-based frame selection and abstention, not physical
  mesh geometry, depth seating, ink quality, AUC, or readable text.
- “11 samples” counts top-level public sample names, not independent scrolls.
