# Manuscript vs. repository: gap review

Manuscript: *Reaction-aware control of autonomous batch synthesis* (Ye, Ryu, *et al.*), submitted version of 2026-05-20 (18 pp.).
Repository state reviewed: upstream commit `01278f84fd592dd0afdffaeec1bd71f6105af90e` plus the offline/simulation overlay (see `Code/README_offline.md`).

This document lists what the manuscript states that the repository does not implement, does not document, or implements differently. Each row ends with a suggested resolution: change the **paper** text, change or add **repo** material, or both. It is a review aid and makes no claims beyond what the code and data show.

Paths are relative to the repository root. `sj/` abbreviates `Code/src/science_jubilee/`.

Verdicts: **OK** (matches), **PARTIAL**, **ABSENT** (not in repo), **CONTRADICTS** (repo does something different).

---

## 1. Core method: the committed hardware loop does not run the published policy

The paper's method (Fig. 1b, Fig. 4a, Methods) has three parts:
- `k_n` is the BO objective.
- The extent of reaction at the first follow-up, α(t₁), switches between 5 vials / 15 min and 2 vials / 5 min, with a threshold of 0.5.
- The optical gates (400 nm, 590 nm) decide which trajectories produce a `k_n`.

The committed orchestrator implements a different policy.

> **Update (overlay, not yet on `main`):** `sj/optimization/reaction_aware.py` (`ReactionAwareBOLoop`) now implements the paper's policy and is the default for `Code/run_bo_optimization.py` and `Code/multi_phase_bo_orchestration.ipynb`. It covers rows 1.1, 1.2, 1.4 and 1.6 for future runs. The loop:
> - uses `k_n` behind the optical gates as the objective;
> - computes α(t₁) for every batch, and moves from 5 vials / 15 min to 2 vials / 5 min once α(t₁) > 0.5;
> - starts from the campaign seeds and uses the selection settings of `bayesian.ipynb`;
> - tracks vial positions and pauses for the operator at phase changes and when the racks are full.
>
> `Code/tests/run_reaction_aware_check.py` replays `Dataset/` through the loop. All 9 recorded batches are reproduced (formulations, batch sizes and intervals), and the switch comes after BO_4 at α(t₁) = 0.648, which settles row 1.3. Two `k_n` values have to be supplied by hand: BO-2 B1 (row 3.2b) and BO-7 A3 (row 3.2a).
>
> The table below still describes the legacy `orchestrator.py`. The published data were produced through the notebooks, not by this loop.

| # | Paper | Repository | Verdict | Suggested resolution |
|---|---|---|---|---|
| 1.1 | BO objective is the Gualtieri nucleation rate constant `k_n` | `batch_extract_yields` returns **`I_max`** per vial (`sj/analysis/gualtieri.py:474-481`). The orchestrator trains the GP on it (`sj/optimization/orchestrator.py:285-304`). `K_n = 1/a` is computed (`gualtieri.py:276`) but never reaches the optimizer | CONTRADICTS | Repo: state in README that `run_bo_optimization.py` optimizes `I_max` and that the campaign's `k_n` objective was run through `Code/bayesian.ipynb`. Alternatively, add a `k_n` objective option |
| 1.2 | α(t₁) > 0.5 switches to 2 vials / 5 min; otherwise 5 vials / 15 min | No α(t₁) computation in committed `.py` code. `DEFAULT_PHASES` is a one-way exploration → refinement → validation schedule (batch 10 → 5 → 2, xi 0.05 → 0.01 → 0.001), gated on best **`I_max`** ≥ 0.1 / 0.5 (`sj/optimization/phase_config.py:67-103`, `sj/optimization/phase_manager.py:116-149`). The interval is hard-coded to 15 min × 13 readings for every batch (`orchestrator.py:187-188`), so there is no 5-min mode | ABSENT / CONTRADICTS | Repo: document the difference. The only implementation of the paper's rule is `sj/offline_demo.py:198-207` (`mode_decision`), which is **not committed** |
| 1.3 | In the campaign the switch was driven by α(t₁) | `Code/bayesian.ipynb` sets `k=5` by hand for rounds 1–4 (cells 21–30) and `k=2` for rounds 5+ (cells 33–45). No cell computes α(t₁). The 15 → 5 min interval change is visible only in the `Dataset/` plans and logs | PARTIAL | Paper or SI: say the mode switch was applied by the operator according to the rule. Repo: add the α(t₁) values that triggered the switch (after BO_4) |
| 1.4 | α(t) is the **normalized** 400 nm signal (unitless) | Upstream only subtracts a baseline: `ys - ys[0]` (`gualtieri.py:392-393`) and `y - nanmin(y)` (`:247`). There is no division by a plateau or maximum, so the fitted signal is ΔA in a.u. The normalized α used for the 0.5 rule exists only in the overlay (`offline_demo.py`) | PARTIAL | SI S5: define the normalization used for α(t₁) exactly. Repo: commit that definition |
| 1.5 | The 0.5 threshold is justified by the Fig. 3d–g truncation study | No code or derived data for the truncation study (see §3) | ABSENT | Repo: add the analysis script |
| 1.6 | Three outcome classes (negligible reaction, non-ZIF product, ZIF-67) from the 400 nm baseline and 590 nm feature. `k_n` is extracted only from ZIF-67 trajectories | No 590 nm handling or gates in any committed `.py` file. Failed fits enter the GP as `I_max = 0` (`gualtieri.py:477-479`). The classification exists only in `Code/gualtieri.ipynb` (cell 15) and the overlay | PARTIAL | SI: give the numeric gate thresholds (the overlay uses: 400 nm max and range ≥ 0.12 a.u., 590 nm range ≥ 0.12 a.u.) and state that the notebook applies them |
| 1.7 | BO starts from 5 maximin seeds and runs "until convergence" | Greedy maximin seeds on a 0.1 mL lattice (`sj/optimization/sampling.py:67-127`, `n_initial_samples=5`). Convergence rules are in `sj/optimization/convergence.py:150-215` | OK | Paper or SI: say which convergence rule ended the 33-vial campaign, since `bayesian.ipynb` has no automatic stop |
| 1.8 | GP and acquisition details | Matern-5/2 ARD + White kernel, analytic EI, greedy batch selection with an exclusion radius, 8 mL total on a 0.1 mL lattice. The exclusion radius is `radius_uc=0.18` in the orchestrator (`sj/optimization/orchestrator.py:504`) but `0.2` in `bayesian.ipynb` | Not in main text | SI: list kernel, xi = 0.01, radius 0.2 and lattice, matching the notebook that was actually used |

## 2. Execution and Methods

| # | Paper | Repository | Verdict | Suggested resolution |
|---|---|---|---|---|
| 2.1 | Per reaction: TEA by single syringe, then Co(NO₃)₂ and 2-mIm by dual syringe | Steps run batch-wise. TEA goes into **all** vials, then Co into **all** vials (`sj/Experiment.py:142-146`). Then, vial by vial: 2-mIm, a 3 mL single-syringe **mix**, and the t0 spectrum (`Experiment.py:149-161`, `555-567`). Co therefore sits in every vial before any 2-mIm is added | CONTRADICTS (order and granularity) | Paper Methods: describe the batch-wise order and the mixing step |
| 2.2 | Stocks: Co 10 mM, 2-mIm 100 mM, TEA 5 mM in methanol | Concentrations are not recorded anywhere (plans or metadata). Only names are stored: `"Co+2"`, `"triethylamine/methanol"` (`Code/draft_synthesis_plan.json`, `sj/utils/synthesis_plan.py:20-22`) | ABSENT | Repo: add the concentrations to the plan metadata or the README |
| 2.3 | 2 oz reservoirs; 3-dram vials | Reservoir labware is named `60000ul` with 40 mL usable per well. Sample-vial labware is `14000ul`, 20 mm diameter (`sj/labware/labware_definition/uwsunlab_10_wellplate_14000ul_*.json`). A 3-dram vial holds about 11 mL | Unclear | Check the vial size, then align either the paper or the labware names |
| 2.4 | Rule-based lid skip when the gap between measurements is short | The lid is replaced only if the next reading is more than **1 min** away and every vial has been made (`Experiment.py:660`). During synthesis, lids are never replaced | OK (threshold not stated) | Paper or SI: state the 1-min rule |
| 2.5 | Cyclic scan of A1–A5 at successive timepoints | `record_spectrum_till_end` (`Experiment.py:772-`) measures whichever vial is due soonest. It starts each reading 94 s early (`:810`) and takes **three** scans per timepoint (`:620-625`). Recorded times are nominal, `(n-1)·interval` (`:618`). Actual times appear only in `operations_log.txt` and are used by the fits | PARTIAL | SI S3/S4: describe the triplicate scans, earliest-due-first ordering and the nominal vs. logged times |
| 2.6 | Spectra acquired immediately after mixing, then every 5 or 15 min | The t0 spectrum is taken in `make_vial` right after mixing (`Experiment.py:567`). Each vial gets 13 readings: 0–60 min at 5-min intervals or 0–180 min at 15-min intervals | OK | Paper: state the 13 readings and the 60 / 180 min windows. These matter for Fig. 3d–f |
| 2.7 | Control module is `experiment.py`, run in a "module-level verification mode" | The file is `Experiment.py`. It has no verification mode; the only entry point is `make_batch` (`Experiment.py:96`). Running components one at a time is done in loose cells of `Code/mof_synthesis.ipynb` | ABSENT | Paper: fix the filename and describe the mode as used (step-wise notebook execution) or drop the term |
| 2.8 | Ocean Optics transmission dip probe; four toolheads | Tools T0 single syringe, T2 dual syringe, T3 spectrometer (`sj/tools/Oceandirect_axo.py`) and T4 vacuum gripper match | OK | — |
| 2.9 | Hardware calibration data (SI S2) | `sj/calibration/` has only Jubilee motion and tool-offset notebooks. Volume calibration is a single `mm_to_ml` constant per syringe config (`sj/tools/configs/*.json`). There is no gravimetric or spectrometer calibration data | ABSENT | Repo: add the S2 calibration data or point to the SI |

Latent upstream defects found during the review are listed here and not fixed; upstream hardware code is left unmodified:
- `all_vials_made` (`Experiment.py:196-225`) and `nearest_spectrum_reading` (`:670-720`) return inside the per-slot loop, so only the first slot is checked. This matters for plans that use two slots; BO-7 used A3 and B3.
- `record_spectrum_if_needed` (`:722`) is never called and references an undefined `self.make_vial_time_mins` (`:754`).

## 3. Figures and quantitative claims

| # | Paper | Repository | Verdict | Suggested resolution |
|---|---|---|---|---|
| 3.1 | 35-run maximin baseline; 21 of 35 gave ZIF-67; best `k_n` = 0.43 min⁻¹ | `Code/sampling.ipynb` (`n_init=35`, seed 42) plus `Code/gualtieri.ipynb`. The best is benchmark-6 B3 at 0.4307. Data: `Dataset/initial sampling_*` and `benchmark sampling-1…6` (7 × 5 vials) | Reproducible | Paper: state that the 5 initial-sampling vials are shared by both arms (35 maximin, 33 BO) |
| 3.2 | 33-vial BO campaign; best `k_n` = 2.33 min⁻¹; 5.4-fold improvement | 5 seeds + `BO_1`…`BO_4` (4 × 5 vials, 15 min) + `BO-5`…`BO-8` (4 × 2 vials, 5 min) = 33. The best is BO-5 A1 at 2.3337, and 2.3337 / 0.4307 = 5.42 | Reproducible, with two problems | Repo: (a) Round 7 data are missing. `gualtieri.ipynb` cells 148–151 fit `BO_7_Zuyang_20251124_131926/` (a 13:19 run). This folder is not in `Dataset/` and was never committed. The repo instead holds `BO-7_Zuyang_20251124_105621/`, an earlier run (10:56–12:10) of the same two recipes (A3 2.5/2.7/2.8 mL, B3 0.1/7.9/0.0 mL), which gives different data: A3 has plateau `I_max` 0.578 versus 0.193 in the notebook's saved output. `bayesian.ipynb` cell 41 trains on the missing run's `k_n` = 1.94775, so round 8 cannot be reproduced from the repo. Add the 13:19 folder and document why BO-7 was rerun. (b) Cell 110 drops the 45-min point of BO-2 B1 by hand (`remove_times=[45]`); the paper or SI should say so |
| 3.3 | Fig. 5c: the same trajectory refitted at 15-min sampling gives `k_n` = 0.362 min⁻¹ | No code subsamples or refits a trajectory, and 0.362 appears nowhere in the repo. `gualtieri_from_csv(..., remove_times=...)` could do it | ABSENT | Repo: add the refit script |
| 3.4 | Fig. 3c–g: fast, medium and slow trajectories; `k_n` and α vs. total duration and frequency (1, 1.33, 2, 4 h⁻¹); α(final) and α(t₁) | Raw spectra exist. No truncation or resampling code, no choice of representative vials, no α(t₁) table | Data only | Repo: add the analysis script and name the three representative vials |
| 3.5 | Fig. 3a three outcome classes; Fig. 3b pathway schematic | The classification can be reproduced (`gualtieri.ipynb` cell 15). There is no plotting code for 3a; 3b is a schematic | PARTIAL | Optional |
| 3.6 | Fig. 4b: per-batch time, manual vs. robotic, split into synthesis and characterization | No timing code. Manual-operation timings are not in the repo | ABSENT | Repo: add the manual timing data and a script that derives robot phase times from `operations_log.txt` timestamps |
| 3.7 | Fig. 4c, 5f–h: cumulative machine time (−8.3 %), decision interval per iteration (−65 %), measurement throughput (3×) | No code. The robot code records no synthesis or characterization timers. All of these could be derived from log timestamps, but the definitions (what "decision interval" and "throughput" count) are not written down anywhere | ABSENT | SI: define each metric. Repo: add the script |
| 3.8 | Fig. 5a: ternary surrogate heatmap of `k_n` | `bayesian.ipynb` plots an **EI** heatmap (cell 17); μ is computed but not plotted | PARTIAL | Repo: add the μ plot |
| 3.9 | Fig. 5b optimization trajectory; Fig. 5d swarm plot | Absent. The only best-so-far plot (`multi_phase_bo_orchestration.ipynb`) shows live `I_max`, not the recorded campaign | ABSENT | Repo: add the plotting script |
| 3.10 | Platform assembled for less than $3,000 (SI S8) | `docs/Axo_bill of materials.xlsx` section subtotals: 2207.28 + 150.17 + 199.12 + 71.89 + 279.37 = **$2,907.83** without the "Tool – Spectrometer" section (**$10,779.05**, Ocean HDX). Including it gives $13,686.88 | Holds only without the spectrometer | Paper: say "excluding the spectrometer" |

## 4. Availability metadata

| # | Paper | Repository | Suggested resolution |
|---|---|---|---|
| 4.1 | Data and code at `https://github.com/uwsunlab/Axo_MOF.git` | The git remote is `https://github.com/camsunlab/Axo_MOF.git`. `README.md`, `Code/pyproject.toml`, `docs/offline_validation.md` and `offline_example/provenance.json` all cite `camsunlab` | Align the org name; check that the `uwsunlab` URL redirects |
| 4.2 | Zenodo DOI `10.5281/zenodo.19664270` | The README badge cites `10.5281/zenodo.19664269` | Check on Zenodo whether these are the concept DOI and the version DOI. Cite the one intended (usually the concept DOI) in both places |
| 4.3 | Title | The README heading matches the paper title | OK |
| 4.4 | "All relevant code" | The offline demo, simulation, packaging, tests and this document are overlay files on a branch, **not yet on `main`** | Merge and release them before citing them |

## 5. Summary

- The Bayesian optimization, Gualtieri fitting, maximin seeding, logging and tool control described in the paper are all in the repository.
- The **reaction-aware** part, which is the paper's main contribution, is not in the committed code that drives the robot:
  - The orchestrator optimizes `I_max`, not `k_n`.
  - It applies no optical gates.
  - It never computes α(t₁).
  - It keeps a fixed 15-minute measurement interval.
- The campaign itself was run as notebook steps (`gualtieri.ipynb` → `bayesian.ipynb`), with the operator applying the mode switch by hand.
- The rule as published is implemented only in the overlay (offline demo and `reaction_aware.py`), not yet on `main`.
- The quantitative results that support the policy (Fig. 3d–g, Fig. 4b/c, Fig. 5c and 5f–h) have raw data in `Dataset/` but no analysis code.
- Smaller mismatches:
  - dispense order (§2.1);
  - stock concentrations not recorded (§2.2);
  - "module-level verification mode" (§2.7);
  - "$3,000" holds only without the spectrometer (§3.10);
  - repository URL and DOI (§4.1–4.2).
