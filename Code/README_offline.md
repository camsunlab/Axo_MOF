# Axo offline reproducibility example

This example runs one complete **recorded-data analysis and recommendation cycle**:
UV–Vis CSV files → optical screening → Gualtieri fitting → GP/EI calculation →
proposed next conditions and measurement schedule. It does not connect to a robot,
load Ocean Optics drivers, acquire spectra, or execute a proposed experiment.

## Install

Use a separate Python 3.12 environment. The reference interpreter is Python
3.12.14; exact Python and operating-system details of the verified run are in
`docs/offline_validation.md`. `requirements-offline.txt` pins direct and
transitive dependencies; platform-specific dependencies use environment markers.

Run from the **repository root** of an upstream checkout at commit
`01278f84fd592dd0afdffaeec1bd71f6105af90e`, after copying the supplementary files
over it (see "Reproducible offline analysis" in the top-level `README.md`):

```bash
python -m venv .venv
```

Activate on Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Or on Linux/macOS:

```bash
source .venv/bin/activate
```

Then install:

```bash
python -m pip install -r Code/requirements-offline.txt
python -m pip install --no-deps --no-build-isolation -e ./Code
python -m pip check
```

The initial installation requires access to the Python package index. After
installation, running the example requires neither internet access nor hardware.
Use the same commands in a new environment to reproduce the pinned environment.
The Python interpreter itself is selected separately; `.python-version` records
the reference patch version and does not install Python automatically.

## Run from the command line

```bash
python Code/offline_demo.py --output offline_demo_outputs
```

The equivalent installed entry point is:

```bash
axo-offline-demo --output offline_demo_outputs
```

No local paths or instrument addresses need to be edited. The input is the
bundled copy of `Dataset/BO_4_Zuyang_20251117_190856`. To process another recorded
batch with the same schema, pass its folder explicitly:

```bash
python Code/offline_demo.py --data-dir "Dataset/BO_4_Zuyang_20251117_190856" --output offline_demo_outputs
```

Input files are read-only. Running again replaces the generated files in the
selected output directory. Files outside that output directory are not changed.

## Run the notebook

```bash
python -m ipykernel install --sys-prefix --name axo-offline --display-name "Axo offline (Python 3.12)"
jupyter lab Code/offline_demo.ipynb
```

Select the **Axo offline (Python 3.12)** kernel, then **Restart Kernel and Run All**.
The notebook contains no installation cells and makes no network requests. It
prints the output directory before showing the fitted parameters and plots.

## Expected files

| File | Contents |
| --- | --- |
| `processed_trajectories.csv` | Replicate-aggregated optical signals, nominal times and log-derived elapsed times |
| `kinetic_parameters.csv` | Optical screening status, fitted parameters, R², optimization scores and bound flags |
| `next_conditions.csv` | Proposed compositions, predictive means, uncertainties and expected improvement |
| `next_batch_plan.json` | The proposed batch size and measurement interval; `executed` is always `false` |
| `kinetic_fits.png` / `.svg` | Recorded trajectories and fits; rejected trajectories are identified explicitly |
| `gp_recommendations.png` / `.svg` | GP predictions and proposed conditions |
| `run_summary.json` | Scope, numerical settings, software versions, warnings and decision summary |
| `input_provenance.json` | Immutable source revision and SHA-256 checksums for bundled inputs |

`Code/expected_offline_output/` contains the reference numerical results. Small
floating-point differences can occur across operating systems; the validation
script compares quantities with documented tolerances instead of image bytes.
Fitted parameters that the recorded data do not resolve (B1 `k_g`, and the
parameters of B3, whose fitted `a` lies beyond the observation window) can differ
between platforms at the same R². They are checked more loosely, as described
in `docs/offline_validation.md`.

## What is reproduced

- The original `read_uvvis`, closest-two replicate averaging, log-time parser,
  `fit_gualtieri`, GP, expected-improvement and batch-selection functions are reused.
- Optical screening follows the deposited `gualtieri.ipynb`: the 400-nm maximum
  and range must reach 0.12 a.u.; the 590-nm range must also reach 0.12 a.u.
  These are operational optical gates, not independent proof of product identity.
- The fitter subtracts the minimum observed signal and optimizes four parameters,
  including `I_max`, with the original multistart grid and bounds. No observations
  are manually removed. The normalized optical signal is divided by the fitted
  `I_max` and is not clipped. A fitted `a` beyond the observed time window is
  flagged explicitly rather than treated as a directly resolved nucleation time.
  This follows the deposited fitting implementation;
  it is not the separate fixed-amplitude repeatability analysis.
- Recorded `Spectrum recorded` events define elapsed times, relative to the
  baseline visit for each vial. These are not exact detector-exposure times or
  time since reagent addition. Missing log matches cause an error rather than a
  silent substitution of nominal times.
- The optimization objective is explicitly `k_n = 1/a`. Samples rejected by an
  optical gate receive a **zero optimization score**, following the notebook
  convention. This zero is not a measured physical nucleation rate.
- The GP uses only this recorded five-vial batch. Parameters are fixed at seed
  42, 20 optimizer restarts, an 8-mL total, a 0.1-mL lattice, EI offset 0.01 and
  local exclusion radius 0.2 in unit coordinates. Previously measured points in
  this supplied batch are excluded from the proposal.
- The two-mode example applies the criterion that any fitted trajectory with
  first-follow-up normalized optical progress above 0.5 selects a two-vial,
  5-min schedule; otherwise it proposes five vials at 15-min intervals. This is
  a one-step decision example, not a counterfactual replay of the campaign.

The proposed conditions are **not expected to match the historical next batch**:
the full earlier training history is deliberately not reconstructed here. This
example also does not establish kinetic-parameter certainty or quantify the
benefit of adaptive execution.

## Computational analysis versus hardware operation

The original generic hardware orchestration code remains available. Its
`batch_extract_yields` interface returns `I_max`, and its default phase
configuration is a separate generic multistage example. The offline example
does not use those defaults as the manuscript's kinetic objective or two-mode
policy, and does not alter the existing hardware controller.

Running physical experiments additionally requires the compatible Jubilee/Duet
platform, syringe tools, spectrometer, OceanDirect SDK and OS-specific vendor
drivers. Deck geometry, tool offsets, syringe calibration, instrument selection,
reagent assignments and local output paths must be configured for the installed
apparatus. Consult the existing build and API documents under `docs/` and the
configuration files under `src/science_jubilee/`. The offline environment is not
a validated instrument-driver installation or a physical simulator.

## Simulation mode

### What the simulation shows and doesn't show

The offline demo above and the simulation mode answer different questions. The
demo reanalyses **real recorded data** from one batch. The simulation runs the
**whole optimization loop** over many rounds on **synthetic data**. The loop has to
use synthetic data: each round proposes compositions that were never synthesized,
so recorded data cannot supply their results.

- **It shows** that the upstream optimization software runs end to end without
  hardware (planning, analysis, model updates, phase changes, saving and resuming),
  that both loops find the optimum of a problem whose answer is known, and where
  the design has weak points.
- **It does not show** which composition is best for real ZIF-67, how well the
  historical campaign performed, or anything about the robot itself. Only the
  decision loop runs; `Experiment`, `Machine` and the tools are replaced or
  unused. The optima it reports belong to the synthetic formula, not to the
  chemistry.

Details: `docs/simulation_validation.md`.

### Running it

The simulation mode runs the multi-round autonomous loop without a robot or
spectrometer. A simulated lab stands in for `Experiment.make_batch`: for each
planned vial it writes UV–Vis CSVs, an `operations_log.txt`, `mof_recipes.json` and
`experiment_metadata.json` in the robot's own formats, from a **synthetic ground
truth**. Every file is marked as simulated. Nothing waits in real time.

```bash
axo-simulate --output simulation_outputs            # or: python Code/simulate.py ...
axo-simulate --quick --output simulation_outputs    # short run, about 30 s
```

Two loops run against the same simulated lab:

| | Loop A: `upstream/` | Loop B: `policy/` |
| --- | --- | --- |
| Code | Upstream `BayesianOptimizationLoop`, unchanged; only the "press ENTER when vials are loaded" prompt is skipped and phase transitions are approved automatically | The offline demo's analysis, repeated over rounds |
| Objective | `I_max` from upstream `batch_extract_yields`; no optical gates | `k_n = 1/a`; optical gates, gate failures score 0 |
| Scheduling | Upstream `DEFAULT_PHASES` (10 → 5 → 2 vials, 15-min readings) | Two-mode rule: 5 vials at 15 min, or 2 vials at 5 min |
| Training data | All batches so far | All rounds so far |

Loop B assumes 13 readings per vial in both modes (the upstream plan default), so
a 5-min batch covers 60 min. Loop A's settings come from upstream
(`n_initial_samples=5`, seed 42 for sampling and the GP); `--seed` changes only
the simulated measurement noise. `--max-total-iterations` (default 25) stops
Loop A if it has not converged; this is reported as `stopped_by_cap`.

Outputs in `simulation_outputs/`:

| Path | Contents |
| --- | --- |
| `upstream/batches/`, `policy/batches/` | One folder per simulated batch in the recorded `Dataset/` layout, plus `ground_truth.json` with the true parameters of each vial |
| `upstream/bo_state.json`, `upstream/bo_optimization.log`, `upstream/synthesis_plan_*.json`, `upstream/plots/` | The upstream loop's own state, log and plots |
| `upstream/summary.json`, `policy/summary.json`, `policy/vials.csv` | Per-iteration decisions and the true value of the best measured point |
| `progress.png` | True value of the best measured point relative to the true optimum, per batch |
| `simulation_summary.json` | Ground-truth constants and optima, both summaries, software versions |

**Scope.** The ground truth is an illustrative formula
(`science_jubilee/simulation/virtual_lab.py`, `GroundTruth`), not a fit to the
campaign. Low Co gives no reaction and low 2-mIm gives no 590-nm feature, as for
B4 and B2 in the recorded batch. Results show that the software runs end to end
and behaves as designed on a problem with a known answer. They are not evidence
about ZIF-67 kinetics, about optimization performance on the physical system, or
about the historical campaign. See `docs/simulation_validation.md`.

Two upstream defects are worked around in the simulated experiment, not fixed in
the hardware code: `Experiment` never sets the `experiment_dir` attribute that the
orchestrator reads after each batch, and the spectrometer writes every batch into
one folder. Both are described in `docs/simulation_validation.md`.

## Validation

After a complete demo run:

```bash
python Code/tests/verify_offline_outputs.py --output offline_demo_outputs
```

To repeat the complete pipeline with network connections and hardware-module
imports blocked:

```bash
python Code/tests/run_offline_isolation.py --output offline_demo_outputs
```

The second command is an actual recorded-data offline check, not an instruction
to interrupt a live hardware command. The validation report documents what was
executed and the scope of its evidence.

To check the simulation mode, with the same network and hardware-import blocking:

```bash
python Code/tests/run_simulation_check.py --output simulation_check_outputs
```

It checks that noise-free simulated files read back through the upstream parsers
and fitter recover the true parameters within 1 %, that low-Co and low-2-mIm vials
fail the expected optical gates, that the upstream loop runs and resumes from
`bo_state.json`, that the two-mode loop switches mode and proposes only untested
lattice points, and that two runs with the same seed write byte-identical files.
