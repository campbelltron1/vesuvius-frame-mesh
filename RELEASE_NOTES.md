# v0.1.3 — eight-case consequence evidence

This evidence-only update leaves the v0.1.2 resolver behavior unchanged and
adds a preregistered eight-case PHerc0139 consequence test.

Highlights:

- mean fixed-canvas native-minus-wrong-frame AUC of +0.01845;
- 7/8 positive cases and bootstrap 95% interval [+0.00823, +0.02872];
- 36/36 locked alignment and evaluation-mask sensitivities retain GO;
- exact sign and mean sign-flip checks;
- complete case JSON and prediction hashes;
- displacement table plus disclosed, outcome-selected explanatory panels; and
- an explicit bound: after separate label-assisted alignment, the mean AUC
  advantage is only +0.00173.

The clean release repository now passes 112 tests. The supplied release
verifier builds twice, installs the wheel into a temporary environment,
exercises both commands, exact-compares the frozen 650-case metadata replay,
and verifies the packaged multi-case evidence. Resolver runtime code is
unchanged from v0.1.2.

## v0.1.2 — initial public release

The initial release introduced the fail-closed single and bounded batch
resolvers, canvas verification, atomic metadata caching, checkpoint/resume,
the 650-case public-corpus audit, and deterministic release builds.
