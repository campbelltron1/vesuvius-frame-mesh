# Manifest versus frozen-walk inventory

This note reconciles the 902 target-specific mesh paths obtained from
`metadata.json` with the 860 paths reported by this repository's frozen
directory-walk corpus.

## Result

The difference is sample scope, not another directory-discovery miss.

- The manifest contains 902 matching paths across 11 samples and 32 target
  volume ids.
- The frozen walk covers nine of those samples.
- Within those nine samples, the normalized path sets match exactly: 860/860,
  with no path unique to either method.
- The remaining 42 manifest paths are from samples outside the frozen corpus:
  36 from `PHerc0009B` and 6 from `PHerc0841`.

| sample | manifest | frozen walk |
|---|---:|---:|
| `PHerc0009B` | 36 | — |
| `PHerc0139` | 189 | 189 |
| `PHerc0172` | 106 | 106 |
| `PHerc0343P` | 16 | 16 |
| `PHerc0500P2` | 139 | 139 |
| `PHerc0800` | 6 | 6 |
| `PHerc0814` | 56 | 56 |
| `PHerc0841` | 6 | — |
| `PHerc1447` | 15 | 15 |
| `PHerc1667` | 79 | 79 |
| `PHercParis4` | 254 | 254 |
| **total** | **902** | **860** |

The original wording “the live bucket contains 860 target-specific
directories” was therefore too broad. The supported wording is “the frozen
nine-sample corpus contains 860 target-specific directories.” The 650/650
volume-to-target-mesh result is unchanged for that corpus and makes no claim
about the two additional samples.

## Comparison method

The manifest side uses every `tifxyz-transformed` origin under each sample's
`segments` and `volumes`, restricted to leaf names matching:

```text
-on-(20\d{12})-[\d.]+um\.tifxyz$
```

The walk side uses every matching `meshes[].dir` leaf in the three frozen
corpus documents. Its compact `segment + leaf` representation is normalized
to the manifest's explicit `segment/mesh/leaf` path before exact string-set
comparison. No network request, mesh coordinate pixel, CT voxel, surface
volume chunk, prediction, or label is read.

## Frozen inputs

- `metadata.json` snapshot SHA-256:
  `40517076dc5f2128599868b337e25a17922ebe70a22b238b990e109b6f82af0f`
- PHerc0139 corpus SHA-256:
  `9000d3c672f90d7b3f9396dc49bfcc65774c172e7638061a6986222615d32bd7`
- validation corpus SHA-256:
  `9512169411183d4232fba1b6699ef195d799fc21f023acf3f8db954a14276a17`
- control corpus SHA-256:
  `371114ad9e52b5797307ad58ac9c71d3249d0bddbe8e4d6d6a7f9ed233c85811`

This is a scope reconciliation, not an ink-accuracy result or an extension of
the frozen nine-sample evaluation.
