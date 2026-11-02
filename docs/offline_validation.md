# Offline validation report

Validation recorded: 2026-10-26 00:18 UTC.

## Environment and installation

- Source repository: https://github.com/camsunlab/Axo_MOF
- Base source revision: `01278f84fd592dd0afdffaeec1bd71f6105af90e`.
- Interpreter: Python 3.12.14.
- Platform: Linux-6.18.55-x86_64-with-glibc2.39.
- A fresh virtual environment was created without system site packages.
- All 109 applicable direct and transitive dependency pins matched the installed distributions.
- Editable installation from `Code/pyproject.toml` succeeded.
- A wheel was built, then installed without dependencies into the second, clean pinned environment.
- `python -m pip check` reported no broken requirements.
- The installed `axo-offline-demo --help` entry point succeeded.
- Wheel SHA-256: `f1d083ab9a2d4db4c13298180386db545925de7a375848a7aa0e7d014da1cadc`.

The command-line example was executed from outside the source checkout, using
the installed wheel and its bundled input files. No OceanDirect SDK or live
robot was installed or connected for this validation.

## Recorded-data run

- Input batch: `Dataset/BO_4_Zuyang_20251117_190856`.
- All eight bundled source-file hashes were verified before processing and remained unchanged afterward.
- Five recorded vials and 13 time points per vial were processed.
- B1, B3 and B5 passed the optical gates and were fitted.
- B2 was classified as `no_target_feature`; B4 as `no_reaction`.
- Maximum first-follow-up normalized optical progress: 0.6482889976.
- The example selected a two-vial, nominal 5-min next-measurement schedule.
- Two feasible next conditions were proposed; no physical experiments were executed.
- Run time for the isolated execution: 16.99 seconds on this host; runtime is not a cross-machine guarantee.

The full run succeeded while Python network connections/DNS lookups and hardware
module imports were blocked. The guard loaded no hardware modules. The saved
plan has `executed: false`. See `Code/expected_offline_output/isolation_check.json`.

## Numerical and notebook checks

The clean installed-wheel run and the notebook run agreed with the independently
generated reference outputs. Checks included optical-gate classifications,
`k_n = 1/a`, the scheduling decision, an 8-mL total, 0.1-mL lattice membership,
nonnegative volumes, unique proposals and exclusion of the already tested
formulations in this supplied batch.

Parameter tolerances: relative `1e-3`, absolute `1e-6`; R² absolute tolerance
`1e-5`. Reference proposal compositions were identical within `1e-8` mL.
These tolerances are numerical software checks, not estimates of experimental
or fitted-parameter uncertainty.

**Tolerance revision (2026-10-25).** A rerun of the same pinned environment on a
second Linux host (kernel 6.8.0) reproduced every optical-gate status, R² value
(within `2e-10`), `a` for B1 and B5, the scheduling decision and both proposals.
It failed the original check on three parameters that the data do not resolve:
B1 `k_g` (0.750 vs 0.832; growth is saturated at the first follow-up, so the fit
is equally good at any sufficiently large `k_g`), and B3 `I_max` (9.787 vs 9.752)
and α at the first follow-up (0.01078 vs 0.01082), whose fitted `a` lies beyond the
observation window. The upstream notebook's saved B1 fit gives `k_g` = 0.491
at the same R². `Code/tests/verify_offline_outputs.py` now applies:

- relative `1e-3`, absolute `1e-6` to `a`, `b`, `I_max`, `k_n`, the optimization
  score, α at the first follow-up and `k_g` for fits within the observation window;
- relative `1e-2` to those parameters for fits whose `a` lies beyond the window,
  with the window flag itself required to match;
- for `k_g` whose growth term already exceeds `1 − 1e-6` at the first follow-up,
  a check that the growth term remains saturated rather than a value comparison.

The R², proposal and scheduling checks are unchanged. The reference outputs were
not regenerated. `reference_check.json` and `notebook_reference_check.json` in
`Code/expected_offline_output/` are records of the original run and list the
original tolerances.

All 5 notebook code cells were executed sequentially
with IPython in the clean pinned environment. Tables and rich image outputs
were captured and saved into the notebook; there were no cell errors. Notebook
schema validation passed.

**Validation boundary:** this execution host prohibits the socket binding needed
by external Jupyter kernels. TCP and IPC kernel startup attempts were blocked,
so the Jupyter browser interface and external-process kernel launch were not
validated here. Cell execution and computational outputs were validated using
IPython in the same installed environment. Windows and macOS were not tested;
the lockfile contains platform markers for their dependency selection.

## Scientific and implementation scope

This is a one-batch analysis-and-recommendation example. It is not a replay of
the full BO campaign, a physical simulator, or new evidence of optimization
performance. The GP's training history contains only the five supplied vials.
Gate failures receive a zero optimization score, not a measured physical
nucleation rate.

The existing fitting, GP and sampling implementations were not modified.
The demo explicitly uses `k_n`, whereas the original generic hardware
orchestrator has an `I_max` interface and separate multistage defaults; those
hardware examples were not substituted for the manuscript's kinetic policy.

B3 has a fitted `a` beyond the observation window. This behavior of the original
free-amplitude fit is retained and flagged rather than hidden or treated as a
precisely resolved nucleation time. GP hyperparameter-bound warnings are also
retained in `run_summary.json`. They do not prevent the example from executing,
and this software check does not resolve the scientific identifiability issues.

## Unmodified source-function provenance

- `Code/src/science_jubilee/analysis/gualtieri.py` — SHA-256 `6c7f4c6131f09a1378a869cced5d044f6ae528c327745a423c1af10c9545fead`.
- `Code/src/science_jubilee/optimization/bayesian.py` — SHA-256 `d0834f64bbe88f61939dc42212dce88463463ee89a1526931e032e57df29b748`.
- `Code/src/science_jubilee/optimization/sampling.py` — SHA-256 `1bc29a21099d9396ee121d06240aa34d75515f92e3393c57534681d956e74644`.
