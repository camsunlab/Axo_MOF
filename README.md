[![DOI](https://zenodo.org/badge/1102020512.svg)](https://doi.org/10.5281/zenodo.19664269)

# Reaction-aware control of autonomous batch synthesis

> **Reaction-aware autonomous batch synthesis for ZIF-67 crystallization using robotic liquid handling, in situ UV–Vis spectroscopy, and adaptive Bayesian optimization**

This repository contains the software, analysis workflows, and supporting assets for **Axo**, a modular autonomous platform for solution-phase batch synthesis. The platform integrates robotic reagent delivery, programmable tool changing, cyclic in situ UV–Vis measurements, kinetic model fitting, and reaction-aware Bayesian optimization to co-optimize **what formulation to test** and **how the experiment should be executed**.

---

## 🌟 Key Features

### Reaction-Aware Adaptive Execution
- **Adaptive scheduling**: 
  - **Throughput-prioritized exploration**: 5-vial batch, 15 min measurement interval
  - **Measurement-prioritized refinement**: 2-vial batch, 5 min measurement interval
- **Reaction-aware trigger**: Switch to the 2-vial / 5-min validation phase when any fitted vial has early reaction progress `α(t1) > 0.5`
- **Vial bookkeeping**: Tracks the 20 vial positions (two 10-vial racks) and stops for fresh vials when a batch no longer fits
- **Operator checkpoints**: Stops at every phase change; resume with one flag
- **State persistence**: Resume paused or interrupted runs from checkpoints

### Automated Synthesis & Characterization
- **Robotic liquid handling**: Precise dispensing with single/dual syringe systems
- **In situ monitoring**: Cyclic UV–Vis spectroscopy integrated into the batch synthesis workflow
- **Autonomous mixing**: Automated mixing and reaction control
- **Programmable multi-vial workflows**: Batch synthesis and cyclic monitoring across multiple reaction vials

### Intelligent Data Analysis
- **Gualtieri kinetic fitting**: Automated MOF growth curve analysis
- **Reaction-progress extraction**: Automatic extraction of extent of reaction `α(t)` and nucleation rate constant `k_n`
- **Gaussian Process modeling**: Uncertainty-aware surrogate models
- **Expected Improvement**: Efficient exploration-exploitation balance

### Implementation Features
- **Comprehensive logging**: Experiment metadata, synthesis recipes, operation logs, reference spectra, and time-resolved UV–Vis outputs
- **Error handling**: Graceful handling of excluded or failed fits (`no_reaction`, `no_target_feature`)
- **Hardware safety**: Homing checks, collision avoidance, safe Z movements
- **Configuration management**: JSON-based hardware and experiment configs

---

## 📋 Table of Contents

- [System Architecture](#system-architecture)
- [Installation](#installation)
- [Hardware Requirements](#hardware-requirements)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Project Structure](#project-structure)
- [Documentation](#documentation)
- [Testing](#testing)

---

## 🏗️ System Architecture

The platform is organized into **four main abstraction layers**:

```
┌─────────────────────────────────────────────────────────────┐
│                    Experiment Layer                         │
│  High-level workflows, BO orchestration, kinetic analysis   │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                  Deck & Labware Layer                       │
│     Spatial management, well positions, volume tracking     │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                      Tool Layer                             │
│   Single Syringe, Dual Syringe, Spectrometer, Gripper       │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│                    Machine Layer                            │
│    G-code execution, motion control, hardware interface     │
└─────────────────────────────────────────────────────────────┘
```

**Communication**: HTTP REST API to Duet 3 controller (RepRapFirmware)

---

## 💻 Installation

### Reproducible offline analysis

The computational workflow can be run without the robot, spectrometer or vendor
drivers. Use Python 3.12 (reference version 3.12.14) and a fresh virtual environment.

The offline example is distributed as supplementary files that sit on top of the
upstream repository at commit `01278f84fd592dd0afdffaeec1bd71f6105af90e`. The upstream
code alone has no `Code/pyproject.toml` or `Code/offline_demo.py`, and the demo
imports the upstream analysis and optimization modules, so neither part runs by
itself. Clone the upstream repository at that commit, then copy the supplementary
files over it, replacing the existing `README.md` and
`Code/src/science_jubilee/__init__.py`:

```bash
git clone https://github.com/camsunlab/Axo_MOF.git
cd Axo_MOF
git checkout 01278f84fd592dd0afdffaeec1bd71f6105af90e
cp -r /path/to/supplementary_files/. .
python -m venv .venv
```

In Windows PowerShell, use
`Copy-Item -Recurse -Force C:\path\to\supplementary_files\* .` for the copy step.
Skip the copy step if these files are already part of your checkout.

Activate it with `.\.venv\Scripts\Activate.ps1` in Windows PowerShell, or
`source .venv/bin/activate` on Linux/macOS. Then run:

```bash
python -m pip install -r Code/requirements-offline.txt
python -m pip install --no-deps --no-build-isolation -e ./Code
python -m pip check
python Code/offline_demo.py --output offline_demo_outputs
```

The default dataset is bundled with the package. No local path edits are needed.
Package installation initially requires internet access; the installed demo runs
offline. The package configuration supplies an executable `axo-offline-demo` entry
point. The pinned requirements include notebook dependencies and transitive versions.

See [the complete offline instructions](Code/README_offline.md),
[the executable notebook](Code/offline_demo.ipynb), and
[the validation report](docs/offline_validation.md).

### Hardware-dependent execution

Physical operation additionally requires the installed Jubilee/Duet platform,
compatible tools, an Ocean Optics spectrometer, the OceanDirect SDK and vendor
drivers, plus calibration for the local deck, tools, syringes and optics.
The pinned offline environment does not install or validate those proprietary
drivers. Hardware setup and calibration documentation remains under `docs/`.

---

## 🔧 Hardware Requirements

### Core Platform
- **Jubilee 3D printer** (modified for lab automation)
- **Duet 3 controller** running RepRapFirmware
- **Network connection** (Ethernet or WiFi)

### Tools (mounted on tool changer)
- **T0**: Single syringe (precise liquid dispensing)
- **T2**: Dual syringe (parallel dispensing)
- **T3**: Ocean Optics spectrometer (UV-Vis, 200-1100 nm)
- **T4**: Vacuum gripper (lid handling)

### Labware
- SLAS-standard microplates (6-slot deck)
- 10-well sample vials (14 mL capacity)
- 2-well precursor reservoirs (60 mL capacity)

---

## 🚀 Quick Start

### Hardware-dependent command-line examples

These commands require a configured physical platform. The default policy is the
reaction-aware loop of the manuscript (`optimization/reaction_aware.py`; see
[Adaptive Execution](#adaptive-execution)). Its decisions were checked against the
recorded campaign with `Code/tests/run_reaction_aware_check.py` (see [Testing](#-testing)).

**Start a new campaign** (empty vials in the racks on slots 2 and 5):
```bash
cd Code
python run_bo_optimization.py --new --operator "Alice"
```

**Continue after a phase-change pause:**
```bash
python run_bo_optimization.py --resume
```

**Continue after replacing the used vials** (all racks, or only the listed slots):
```bash
python run_bo_optimization.py --resume --vials-replaced
python run_bo_optimization.py --resume --vials-replaced 2
```

**Drop a batch that was interrupted mid-measurement, then continue:**
```bash
python run_bo_optimization.py --resume --discard-pending
```

**Legacy orchestrator** (`I_max` objective, 10/5/2-vial exploration/refinement/validation
phases, fixed 15-min interval; not used for the published campaign):
```bash
python run_bo_optimization.py --policy phases --new --approval-mode manual
```

### Hardware-dependent orchestration notebook

```bash
cd Code
jupyter notebook multi_phase_bo_orchestration.ipynb
```

The notebook provides:
- Step-by-step hardware initialization
- Interactive configuration
- In situ monitoring
- Built-in visualization and analysis

---

## 📖 Usage

### Adaptive Execution

The reaction-aware loop (`ReactionAwareBOLoop`) runs through **three phases**:

| Phase | Goal | Batch Size | Measurement Interval (min) | Readings per vial | Exploration (xi) |
|-------|------|------------|----------------------------|-------------------|------------------|
| **0 Initial sampling** | Maximin seeds | 5 | 15 | 13 (0–180 min) | – |
| **1 Batch BO** (throughput-prioritized) | Broad screening | 5 | 15 | 13 (0–180 min) | 0.01 |
| **2 Validation** (measurement-prioritized) | Resolve fast reactions | 2 | 5 | 13 (0–60 min) | 0.01 |

**Objective:** nucleation rate constant `k_n = 1/a` from the Gualtieri fit at 400 nm, after
the optical gates (400-nm scattering rise and 590-nm ZIF-67 feature); gate failures score 0.
Batches are chosen by GP + Expected Improvement on the 8-mL / 0.1-mL lattice (exclusion
radius 0.2, already-tested formulations excluded).

**Phase transition:** after every batch, the extent of reaction at the first follow-up reading,
`α(t1)`, is computed for each fitted vial. If any `α(t1) > 0.5`, the run moves to phase 2 (one way).

**Operator pauses:** the run saves its state and stops at every phase change, when no rack has
enough unused vial positions for the next batch (a batch always stays within one rack), when a
batch was interrupted, and when a batch cannot be analysed. `decisions.csv` records the
`α(t1)` behind every phase decision and `vials.csv` the per-vial results.

### Command-Line Arguments

```bash
python run_bo_optimization.py [OPTIONS]

Required (choose one):
  --new                   Start new optimization
  --resume                Resume from saved state

Policy:
  --policy {reaction-aware,phases}
                          Reaction-aware loop (default) or legacy I_max phases

Reaction-aware options:
  --vials-replaced [SLOT ...]
                          With --resume: used vials were replaced (all racks, or listed slots)
  --discard-pending       With --resume: drop a batch interrupted mid-measurement
  --alpha-threshold A     alpha(t1) threshold for the validation phase (default: 0.5)
  --max-bo-batches N      Stop after N phase-1 batches without a switch (default: 10)
  --max-validation-batches N
                          Validation batches before the campaign ends (default: 4)
  --max-total-vials N     Optional vial budget
  --no-phase-pause        Do not stop at phase changes

Legacy multi-phase options (--policy phases):
  --approval-mode {manual,automatic}
                          Phase transition approval mode (default: manual)
  --phases {multi,single}
                          Phase configuration (default: multi)

Legacy single-phase options (ignored in multi-phase):
  --batch-size N          Experiments per batch (default: 5)
  --max-iterations N      Maximum iterations (default: 20)

General Options:
  --output-dir DIR        Output directory (default: optimization_results)
  --operator NAME         Operator name (default: Operator)
```

### Workflow Example

```bash
# Start a new reaction-aware campaign
python run_bo_optimization.py --new --operator "Alice"

# The system will:
# 1. Run the 5 maximin seeds (phase 0) in rack slot 2, 15-min interval
# 2. Fit every vial (optical gates, Gualtieri at 400 nm, k_n, alpha(t1)) and stop at the phase change
python run_bo_optimization.py --resume
# 3. Run 5-vial GP/EI batches (phase 1) until a batch has alpha(t1) > 0.5, then stop at the phase change
#    (if the racks fill up first, it stops: replace the vials and resume with --vials-replaced)
python run_bo_optimization.py --resume
# 4. Run 2-vial validation batches at 5-min intervals (phase 2) and report the best k_n
```

### Jupyter Notebook Workflow

See [multi_phase_bo_orchestration.ipynb](Code/multi_phase_bo_orchestration.ipynb) for:
- Interactive hardware setup and calibration
- Configuration with validation
- Progress visualization
- Post-optimization analysis and plotting

---

## 📁 Project Structure

```
Axo_MOF/
├── Code/
│   ├── src/
│   │   └── science_jubilee/
│   │       ├── Machine.py              # Core machine controller
│   │       ├── Experiment.py           # High-level experiment workflows
│   │       ├── tools/                  # Tool implementations
│   │       │   ├── Syringe.py
│   │       │   ├── Double_Syringe.py
│   │       │   ├── Oceandirect_axo.py  # Spectrometer
│   │       │   └── Vacuum_Gripper.py
│   │       ├── labware/                # Labware definitions
│   │       ├── decks/                  # Deck configurations
│   │       ├── analysis/               # Data analysis modules
│   │       │   └── gualtieri.py        # Kinetic fitting
│   │       ├── optimization/           # Bayesian optimization
│   │       │   ├── bayesian.py         # GP modeling, EI
│   │       │   ├── sampling.py         # Initial sampling
│   │       │   ├── phase_config.py     # Phase definitions
│   │       │   ├── phase_manager.py    # Phase transitions
│   │       │   ├── reaction_aware.py   # Reaction-aware BO loop (manuscript policy)
│   │       │   ├── orchestrator.py     # Legacy I_max multi-phase BO loop
│   │       │   ├── convergence.py      # Stopping criteria
│   │       │   ├── notifications.py    # User interface
│   │       │   └── logging_config.py   # Structured logging
│   │       └── utils/
│   │           └── synthesis_plan.py   # Experiment plan generation
│   │
│   ├── bayesian.ipynb                  # BO analysis and visualization
│   ├── draft_synthesis_plan.json       # Example synthesis-plan configuration
│   ├── gualtieri.ipynb                 # Kinetic fitting workflow
│   ├── mof_synthesis.ipynb             # Manual synthesis workflows
│   ├── multi_phase_bo_orchestration.ipynb  # Interactive orchestration notebook
│   ├── run_bo_optimization.py          # Main CLI entry point
│   └── sampling.ipynb                  # Initial/maximin sampling workflow
|
├── Dataset/                            # Experimental data
│   └── {experiment_name}_{timestamp}/
│       ├── references/                # Background/reference spectra
│       ├── spectra/                   # Time-resolved spectral outputs
│       ├── experiment_metadata.json   # Experiment-level metadata
│       ├── mof_recipes.json           # Per-vial synthesis recipes
│       └── operations_log.txt         # Chronological robotic execution log
│
└── README.md                           # This file
```

---

## 📚 Documentation

### For Users
- **[Quick Start Notebook](Code/multi_phase_bo_orchestration.ipynb)**: Interactive guide for running experiments

### Example Notebooks
- **[mof_synthesis.ipynb](Code/mof_synthesis.ipynb)**: Manual synthesis workflows and calibration
- **[bayesian.ipynb](Code/bayesian.ipynb)**: BO analysis and visualization
- **[gualtieri.ipynb](Code/gualtieri.ipynb)**: Spectral analysis and kinetic fitting
- **[sampling.ipynb](Code/sampling.ipynb)**: Initial space-filling / maximin sampling
---

## 🧪 Testing

The offline example is the reproducibility entry point. From the repository root:

```bash
python Code/tests/run_offline_isolation.py --output offline_demo_outputs
python Code/tests/verify_offline_outputs.py --output offline_demo_outputs
```

The first command runs the recorded-data workflow with network connections and
hardware-module imports blocked. The second checks numerical reference outputs,
screening decisions, composition constraints and input-file integrity. The
reference results and execution details are recorded in
[docs/offline_validation.md](docs/offline_validation.md).

The reaction-aware orchestrator has its own check (about 2 min), also with network
connections and hardware-module imports blocked. It replays the recorded campaign
(`Dataset/`) through the loop and requires every batch it plans to match the batch that
was run, then exercises vial-full, phase-change and interrupted-batch pauses on a
simulated platform:

```bash
python Code/tests/run_reaction_aware_check.py --output reaction_aware_check_outputs
```

The simulation mode has its own check, also with network connections and
hardware-module imports blocked:

```bash
python Code/tests/run_simulation_check.py --output simulation_check_outputs
```

It runs the unchanged upstream optimization loop and a multi-round version of
the two-mode policy against a synthetic ground truth. See
[docs/simulation_validation.md](docs/simulation_validation.md). Its results are a
software check, not evidence about the chemistry or the historical campaign.

Hardware connection checks and physical experiments require the local setup and
are separate from these offline tests.
