# Frame-resolver interpretability analysis — fixed plan

Frozen 2026-10-12 Australia/Sydney before calculating the displacement
summary or rendering case-study panels.

## Purpose

Explain what the eight-case AUC result means spatially without creating a new
accuracy endpoint or selecting a more favorable subset.

## All-case displacement summary

For every one of the eight frozen cases, subtract the native prediction's
label-optimal evaluator offset from the wrong-frame prediction's independently
optimal evaluator offset. Report the vector in downsampled evaluator pixels,
full-resolution canvas pixels, and millimetres using 2.399 um per full-
resolution pixel. Report mean, median, minimum, and maximum vector magnitude.

These offsets are an **evaluation alignment burden**, not a direct physical
mesh-registration measurement. They are label-derived, quantized by the
scorer's grid, and can include model/label noise.

## Case-study images

Create two explicitly outcome-selected explanatory rows:

- w044, the largest positive primary AUC delta;
- w039, the only negative primary AUC delta.

For each case show the same frozen label crop, native prediction, wrong-frame
prediction on the fixed native alignment, and wrong-frame prediction shifted
by its independently selected offset difference. Choose the displayed crop by
one fixed rule: the 512 x 512 region with greatest positive-label count within
the already frozen evaluation window. Use one shared prediction intensity
scale within each row and draw the label boundary identically on every
prediction panel.

The images are explanatory. They do not replace the complete table, and the
outcome-based case selection must be disclosed in the figure and result text.

## Descriptive association

Report Pearson and Spearman association between displacement magnitude and
primary AUC delta across all eight cases. With n=8 these are descriptive only;
no causal or population-level inference will be claimed.

## Resource limits

- Existing compact maps and cached labels only; no render or inference.
- One process, two numeric threads, less than 4 GiB incremental memory.
- No public upload or submission.
