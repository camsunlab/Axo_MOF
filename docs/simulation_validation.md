# Simulation-mode validation report

Validation recorded: 2026-10-25, Linux 6.8.0 (x86_64), Python 3.12.14, pinned
environment from `Code/requirements-offline.txt`. Base source revision:
`01278f84fd592dd0afdffaeec1bd71f6105af90e`. No robot, spectrometer or vendor
driver was installed or connected.

## What the simulation shows and doesn't show

The repository has two hardware-free modes that answer different questions:

| | Offline demo (`axo-offline-demo`) | Simulation mode (`axo-simulate`) |
| --- | --- | --- |
| Data | Recorded spectra from one real batch (BO_4) | Synthetic spectra from a formula |
| What runs | One analysis step: optical screening, fitting, mode decision, next proposals | The full optimization loop over many rounds |
| Question | Does the analysis reproduce from real data without the robot? | Does the autonomous software run, and work as a loop, without the robot? |
| Says anything about ZIF-67? | Yes, for those five vials | No |

**Why synthetic data.** An optimization loop proposes new compositions and then
needs their results to continue. Recorded data exist only for compositions that
were actually synthesized, so a loop driven by recorded data stops after its first
proposal. The synthetic ground truth supplies a result for any composition.

**What it shows.**

- The upstream optimization loop runs end to end without hardware: planning,
  analysis, model updates, phase changes, saving state and resuming.
- Both loops reach, or come within 2 % of, the optimum when the optimum is known,
  a check that real experiments cannot provide.
- Weak points: two upstream defects that would break a multi-batch hardware run,
  and the inability of 15-min readings to resolve the fastest reactions (see
  "Full default run" below).

**What it doesn't show.**

- Which composition is best for real ZIF-67. The optima reported here are
  properties of the synthetic formula, not predictions.
- How well Bayesian optimization performed in the historical campaign; nothing is
  replayed.
- Anything about the robot itself. Only the decision loop runs; the experiment
  layer is replaced, and `Machine`, `Experiment` and the tools are never imported
  (see "Hardware coverage" below).

None of its results should be presented as chemical findings.

## Hardware coverage

| Layer | Upstream code | In simulation mode |
| --- | --- | --- |
| Optimization | `BayesianOptimizationLoop`, GP, phases | Runs, unchanged |
| Experiment | `Experiment.make_batch` | Replaced by `SimulatedExperiment`, which writes the files a batch would produce |
| Tools | Syringes, gripper, spectrometer | Not used |
| Machine | `Machine` (G-code over HTTP to the Duet 3 controller) | Not used |

Dispensing, scheduling of readings, motion, tool changes, labware positions,
safety guards, controller and spectrometer communication, and real-time behaviour
are not exercised.

## Synthetic ground truth

`GroundTruth` in `Code/src/science_jubilee/simulation/virtual_lab.py` maps a
composition (Co, 2-mIm, TEA in mL; 8 mL total) to Gualtieri parameters:

- `a = 10 + 24 · [((Co − 3.0)/2)² + ((2-mIm − 2.5)/2)²]` min; `b = 6 + 0.25·a` min;
  `k_g = 0.05 + 0.03·Co` min⁻¹.
- `I_max = 0.75 · s(Co) · exp(−[(Co − 4)² + (2-mIm − 3)²] / 18)` a.u. at 400 nm,
  where `s(v) = 1 / (1 + exp(−(v − 0.4)/0.08))`.
- 590-nm feature amplitude `= 0.8 · I_max · s(2-mIm)`.
- Spectra are a smooth baseline plus Gaussian bands at 400 nm (σ 40 nm) and
  590 nm (σ 30 nm), scaled by the Gualtieri progress at the elapsed time. Three
  replicates per reading carry an offset (s.d. 0.003 a.u.) and per-pixel noise
  (s.d. 0.002 a.u.).
- Each later reading is delayed by a random 0–60 s recording overhead that
  accumulates, as in `Experiment.record_spectrum`. Times are on a simulated clock
  starting 2000-01-01.

The constants are illustrative. They place values in the range of the recorded
BO_4 fits, and make low Co give no reaction and low 2-mIm give no 590-nm
feature, qualitatively like B4 and B2. They were not fitted to the campaign.
True optima on the 0.1-mL lattice: `k_n` = 0.100 min⁻¹ at 3.0/2.5/2.5 mL;
`I_max` = 0.750 a.u. at 4.0/3.0/1.0 mL. The two objectives deliberately have
different optima.

## Check script

`python Code/tests/run_simulation_check.py` passed in 1 min 40 s with network
connections, DNS lookups and hardware-module imports blocked. No hardware modules
were loaded.

| Check | Result |
| --- | --- |
| Noise-free files read back with upstream `read_uvvis`, `extract_series_at_wavelength`, `parse_times_from_operations_log` and `fit_gualtieri` recover the true `a` and `I_max` (15- and 5-min readings) | max relative error 1.2e-5 (limit 1e-2) |
| Optical gates on simulated vials (2.3/3.2, 2.1/0.1, 0.1/2.5 mL Co/2-mIm) | `fitted`, `no_target_feature`, `no_reaction`, as designed |
| Upstream loop, single phase, 3 vials × 2 iterations, then `load_state()` and one more iteration | 3 iterations, then resumed to 14 vials; batch folders numbered without overwriting |
| Two-mode loop, 3 rounds | switched to 2 vials at 5 min in round 2; all proposals on the 8-mL/0.1-mL lattice and untested |
| Determinism: two runs with the same seed | 19 batch and summary files byte-identical |

## Full default run

`axo-simulate --output simulation_outputs` completed in 3 min 8 s on this host.

**Loop A: upstream `BayesianOptimizationLoop`, unchanged (I_max objective, `DEFAULT_PHASES`).**
It converged after 9 iterations and 60 vials: initial 5, exploration 3 × 10, then
refinement 5 × 5. The loop did not reach the 25-iteration safety cap. The best
measured composition was 4.0/3.0/1.0 mL, the true `I_max` optimum (regret 0).
The run ended as "Final phase 'validation' converged" without running a
validation batch. The transition into the last phase and the convergence check
happen in the same iteration, and the convergence check uses the EI history from
refinement. This is upstream behaviour, recorded here and not changed.

**Loop B: k_n two-mode policy, 6 rounds, 24 vials.**

| Round | Mode | Vials | Best measured point (Co/2-mIm/TEA mL) | Measured `k_n` | True `k_n` |
| --- | --- | --- | --- | --- | --- |
| 0 | 5 at 15 min | 5 | 4.8/3.2/0.0 | 0.031 | 0.031 |
| 1 | 5 at 15 min | 5 | 2.7/2.5/2.8 | 0.132 | 0.095 |
| 2 | 2 at 5 min | 2 | 2.7/2.5/2.8 | 0.132 | 0.095 |
| 3 | 5 at 15 min | 5 | 2.7/2.5/2.8 | 0.132 | 0.095 |
| 4 | 2 at 5 min | 2 | 2.7/2.5/2.8 | 0.132 | 0.095 |
| 5 | 5 at 15 min | 5 | 3.1/2.4/2.5 | 0.325 | 0.099 |

The policy found compositions within 2 % of the true `k_n` optimum (0.099 of 0.100 min⁻¹). The run also
shows a limitation of the analysis that the simulation makes visible. Near the
optimum, where nucleation is fastest (`a` ≈ 10 min), 15-min readings cannot
resolve the nucleation step. The fitted `a` was 0.72× and 0.30× the true value
for the two vials closest to the optimum, which inflates their measured `k_n`
(0.132 and 0.325 against true 0.095 and 0.099). With 5-min readings, every
fitted `a` was within 3 % of the truth. The two-mode rule shortens the interval
for the *next* batch, but it does not re-measure the vial that triggered it, so
the inflated values stay in the training data. This is a property of the
synthetic problem and the analysis settings, not a measured result.

## Upstream defects worked around, not fixed

1. `BayesianOptimizationLoop.run_synthesis` reads `self.experiment.experiment_dir`
   (`orchestrator.py`, line 260), but `Experiment` never sets that attribute; only
   the `Spectrometer` tool has one. On hardware, the loop would raise
   `AttributeError` after the first batch.
2. `Spectrometer.experiment_dir` is fixed when the tool is constructed, so every
   batch in a run writes `spectra/<vial>.csv` into the same folder, and
   `batch_extract_yields` reads every CSV in it.

`SimulatedExperiment` gives each batch its own folder and sets `experiment_dir`.
The upstream hardware code is unchanged because a fix cannot be validated without
the robot.

## Other differences between the two loops

Upstream `batch_extract_yields` applies no optical gates and falls back to
nominal times when a log match is missing. The offline analysis applies the gates
and treats a missing log match as an error. In Loop A, low-2-mIm vials that show
no 590-nm feature still score their 400-nm `I_max`.

## Unchanged upstream files

SHA-256 after the runs, identical to the clone at the base revision:

- `analysis/gualtieri.py` — `6c7f4c6131f09a1378a869cced5d044f6ae528c327745a423c1af10c9545fead`
- `optimization/bayesian.py` — `d0834f64bbe88f61939dc42212dce88463463ee89a1526931e032e57df29b748`
- `optimization/sampling.py` — `1bc29a21099d9396ee121d06240aa34d75515f92e3393c57534681d956e74644`
- `optimization/orchestrator.py` — `bc0eafb4dedd6b9492284b9de4a56de2234cd0681ddfab5f698257f26b76a670`
- `Experiment.py` — `259254c777b5aba6df7970339c22746a5b1c933227a55630c1807d0402d7dcb3`

The offline example's tests (`run_offline_isolation.py`, `verify_offline_outputs.py`)
still pass. Splitting `offline_demo.run_demo` into `analyse_batch`, `mode_decision`
and `recommend` left its outputs byte-identical.
