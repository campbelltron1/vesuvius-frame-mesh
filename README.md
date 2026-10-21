# Vesuvius frame-mesh resolver

`vesuvius-frame-mesh` selects the unique public `tifxyz` mesh built in the CT
frame of a requested Vesuvius surface volume. It is a metadata-only preflight:
it reads public listings, JSON, and TIFF header/IFD ranges, but never reads CT
voxels, mesh coordinate pixels, surface-volume chunks, ink predictions, or
labels.

The practical failure it prevents is rendering a mesh from one scan against a
different scan merely because that mesh is newer or appears finer. The target
CT frame is the deciding provenance. If the public metadata cannot establish a
unique answer, the tool abstains.

## Install

Python 3.10 or newer is required. The runtime has no third-party dependencies.

```bash
git clone https://github.com/campbelltron1/vesuvius-frame-mesh.git
cd vesuvius-frame-mesh
python -m pip install .
```

An offline wheel can also be built with `uv`:

```bash
SOURCE_DATE_EPOCH=1791619200 uv build --offline --no-build-isolation
python -m pip install --no-index --no-deps \
  dist/vesuvius_frame_mesh-0.1.3-py3-none-any.whl
```

## Resolve one surface volume

```bash
vesuvius-frame-mesh \
  PHerc0139/segments/20250108000004-w029_2025010827 \
  2.399um-0.22m-78keV-volume-20260102150214.zarr \
  --output resolution.json
```

Bucket keys and full public-bucket URLs are accepted. The command emits one
JSON document and uses stable exit codes:

| exit | status | meaning |
|---:|---|---|
| 0 | `resolved` | exactly one verified candidate |
| 2 | `missing` | no candidate passes |
| 3 | `ambiguous` | more than one candidate passes |
| 4 | `invalid_metadata` | the input or a candidate cannot be validated |

Missing, ambiguous, and invalid cases never guess a mesh.

## Resolve a batch

```bash
vesuvius-frame-mesh-batch \
  --plan plan.json \
  --cache-dir cache \
  --budget-mib 250 \
  --workers 2 \
  --checkpoint progress.json \
  --output decisions.json
```

The plan schema is:

```json
{
  "schema_version": 1,
  "cases": [
    {
      "id": "w029-native",
      "segment": "PHerc0139/segments/20250108000004-w029_2025010827",
      "surface_volume": "2.399um-0.22m-78keV-volume-20260102150214.zarr"
    }
  ]
}
```

Resume an interrupted batch with the same plan:

```bash
vesuvius-frame-mesh-batch \
  --plan plan.json \
  --workers 2 \
  --checkpoint progress.json \
  --resume \
  --output decisions.json
```

The checkpoint is atomically replaced and bound to the exact plan hash and
case inputs. Worker count is deliberately restricted to one or two so the
shared byte budget and cache remain bounded.

## Decision contract

For each requested surface volume the resolver:

1. parses the 14-digit target CT volume id;
2. discovers published mesh directories for the segment;
3. keeps directories that explicitly name the target id;
4. reads stored TIFF dimensions and mesh scale;
5. applies the published `2^-source_group` render term; and
6. requires the predicted canvas to match both `.zattrs` `canvas_size` and the
   level-0 Zarr array shape.

A mesh is returned only when exactly one candidate passes every check.

## Evidence

The frozen 2026-10-10 primary audit covers 650 public surface volumes across
nine sample names. A separately frozen extension adds 42 disjoint selections
from PHerc0009B and PHerc0841:

| check | result |
|---|---:|
| independent metadata implementation | 650/650 uniquely resolved |
| producer disagreements | 0 |
| installed-command full-corpus replay | 650/650, exact selections |
| disjoint extension | 42/42 exact frozen selections |
| combined tested selections | 692 across 11 sample names |
| fresh live drift comparison | 0 added, removed, or changed rows |
| clean release-repository suite | 112 passing tests |
| full development harness | 196 passing tests |

On the externally defined w029 case from Villa issue
[#1845](https://github.com/ScrollPrize/villa/issues/1845), the resolver selects
the mesh already built in the requested 2.399 µm frame. The frozen reproduction
scores AUC 0.89615, versus 0.86069 for the coarse-frame reproduction carried
through a transform: +0.03546 on that one benchmark. The benchmark, checkpoint,
labels, and original accuracy finding are prior work; this repository adds the
deterministic selection and validation preflight.

A separately preregistered paired test then used all eight existing labelled
PHerc0139 C2 windows. With the same target CT, renderer, model, and canvas in
both arms, choosing the native-frame mesh improved mean fixed-canvas AUC by
**+0.01845**; 7/8 cases were positive and the deterministic segment-bootstrap
95% interval was [+0.00823, +0.02872]. All 36 locked alignment-grid and label-
mask sensitivity configurations retained the original GO decision.

The result is principally spatial: when the wrong-frame arm was allowed its own
label-selected alignment, the mean advantage fell to +0.00173 AUC. The resolver
prevents an avoidable placement error; it is not claimed to improve intrinsic
ink features after label-assisted registration.

See [docs/EVIDENCE.md](docs/EVIDENCE.md) and
[docs/EXTENSION_RESULT.md](docs/EXTENSION_RESULT.md) for hashes, reproduction
commands, claim limits, and links to the raw metadata evidence.

## Relationship to Villa PR #1944

[Villa PR #1944](https://github.com/ScrollPrize/villa/pull/1944), by
`@saschaivanov-lgtm`, adds a catalog-only preflight that classifies a selected
entry's frame relationship. This standalone tool is complementary: it searches
published segment directories, selects a unique target-frame mesh only after
canvas verification, and provides bounded batch/checkpoint operation. It is
not a competing claim to PR #1944's implementation.

## Test

```bash
python -m pip install pytest
python -m pytest -q
```

The tests use local HTTP fixtures and do not download CT or mesh bodies.

For the complete release gate (two deterministic builds, temporary wheel
installation, 112 tests, and exact 650-case evidence replay), install `uv` and
run:

```bash
python scripts/verify_release.py
```

The command caps numerical libraries at one thread and writes
`release_verification.json`, `dist/`, and refreshed `SHA256SUMS`.

## Limits

- The 650/650 result covers the audited public corpus snapshot, not every
  historical renderer invocation or future publisher convention.
- The 42/42 extension is a disjoint compatibility check against frozen
  completed outputs, not a second independent implementation or live replay.
- The w029 AUC result remains one external issue-defined benchmark. The newer
  eight-case result is a preregistered fixed-canvas comparison on the existing
  labelled C2 set, not a claim about every scroll or checkpoint.
- Independently realigning the wrong-frame prediction reduces its mean deficit
  to +0.00173 AUC; the demonstrated benefit is chiefly spatial placement.
- The tool verifies public frame and canvas provenance. It does not assess mesh
  geometry, transforms, depth seating, or ink-model quality.
- The `-on-<volume-id>-` convention is cross-checked against canvas geometry.
  If the convention or metadata changes, the tool fails closed.

## Credit

- Villa issue [#1727](https://github.com/ScrollPrize/villa/issues/1727), by
  `@ge-al`, performed the original published-canvas reproducibility audit.
- Villa issue [#1845](https://github.com/ScrollPrize/villa/issues/1845), by
  `@kadenpool`, established the practical ink-reading cost of the wrong-frame
  choice and supplied the benchmark assets. `@nerln` independently reproduced
  its native-frame arm.
Released under the MIT License.
