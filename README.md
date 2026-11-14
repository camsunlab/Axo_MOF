# Axo: kinetics-aware control of autonomous batch synthesis

Axo combines robotic liquid handling, in situ UV-Vis measurements, Gualtieri
analysis and Bayesian optimization. This repository contains hardware source
code, recorded datasets and examples that run without laboratory equipment.

## Start here

| Purpose | Entry point |
| --- | --- |
| Run recorded ZIF-67 data through analysis and a BO recommendation | [Offline instructions](Code/README_offline.md), [offline notebook](Code/offline_demo.ipynb) |
| Exercise multi-round optimization on synthetic data | `python Code/simulate.py --quick --output simulation_outputs` |
| Inspect or configure the integrated ZIF-8 hardware workflow | [autonomous_mof_synthesis.ipynb](Code/autonomous_mof_synthesis.ipynb), [hardware instructions](Code/README_autonomous_run.md) |
| Inspect the November 2026 ZIF-8 campaign records | [ZIF-8 archive](Dataset/ZIF8_20261104/README.md) |
| Check the scope of this revision and remaining reproducibility work | [Revision notes](docs/revision_code_availability.md) |

## Install the offline examples

Clone or download this repository. Run the following commands from the repository
root, using Python 3.12 (reference interpreter: 3.12.14):

```bash
python -m venv .venv
```

Activate the environment with `.\.venv\Scripts\Activate.ps1` on Windows PowerShell,
or `source .venv/bin/activate` on Linux/macOS. Then run:

```bash
python -m pip install -r Code/requirements-offline.txt
python -m pip install --no-deps --no-build-isolation -e ./Code
python -m pip check
python Code/offline_demo.py --output offline_demo_outputs
python Code/simulate.py --quick --output simulation_outputs
```

The root `requirements.txt` is a legacy hardware list; use the pinned
`Code/requirements-offline.txt` for these examples.

Installation needs access to the Python package index. These examples then run
without a robot, spectrometer, OceanDirect SDK or network connection. All required
example inputs are included. `axo-offline-demo` and `axo-simulate` are equivalent
installed commands. No checkout of an older commit or supplementary-file overlay
is required.

The recorded-data example covers one five-vial ZIF-67 batch: spectral processing,
optical screening, Gualtieri fitting and the next BO recommendation. It does not
reconstruct the entire experimental campaign. The multi-round simulation uses
synthetic spectra and is a software demonstration, not experimental performance
evidence.

## Integrated hardware workflow

Use [Code/autonomous_mof_synthesis.ipynb](Code/autonomous_mof_synthesis.ipynb).
The notebook calls `ReactionAwareBOLoop`, the experiment controller and the
analysis/optimization modules. For ZIF-8, `FEATURE_GATE_NM = None` disables the
590-nm ZIF-67 feature gate. The objective is `k_n = 1/a`, and the extent of reaction
at the first follow-up observation determines the next execution level.

The archived ZIF-8 run contains four rounds and eleven vials: 5 vials with 15-min
measurements, followed by three rounds of 2 vials with 5-min measurements. After
round 1, the recorded maximum alpha(t1) was 0.728, above the 0.5 threshold. Mode
switching was automatic. The run paused once for reagent replenishment after
round 2 and was then resumed. The configurable 1-vial/2-min level was not reached
in this recorded campaign.

The public notebook has relative output paths and cleared execution output. The
original executed notebook and saved campaign records are retained separately in
the ZIF-8 archive. See the revision notes for an exact description of these
publication edits.

Physical operation requires the configured Jubilee/Duet platform, syringe tools,
Ocean Optics spectrometer, OceanDirect SDK, vendor drivers and local calibration.
The original hardware notebook records Python 3.11.16. The pinned Python 3.12
environment validates the offline examples; it is not a claim that the historical
hardware run used Python 3.12. Calibration files are apparatus-specific.

The older `mof_synthesis.ipynb`, `multi_phase_bo_orchestration.ipynb` and
`run_bo_optimization.py` remain available. The integrated notebook above is the
entry point for the archived ZIF-8 configuration. The legacy generic phase loop
uses a different objective and must not be described as the ZIF-8 workflow.

## Verification

```bash
python Code/tests/run_offline_isolation.py --output offline_demo_outputs
python Code/tests/verify_offline_outputs.py --output offline_demo_outputs
python Code/tests/run_simulation_check.py --output simulation_check_outputs
python Code/tests/notebook_dry_run.py Code/autonomous_mof_synthesis.ipynb notebook_dry_run_outputs --truth fast --in-process
python Code/prepare_zif8_upload.py --check-root .
```

The notebook test substitutes simulated devices and spectra. It checks software
execution and decisions; it does not establish real hardware reliability. The
last command checks the deposited ZIF-8 input files and their checksums and
reports missing files. GitHub Actions runs these checks on the revision branch,
on pull requests to main and after merging.

## Data, hardware and citation

- Historical ZIF-67 data: `Dataset/`.
- ZIF-8 campaign records and expected raw-data layout: `Dataset/ZIF8_20261104/`.
- Build and hardware documentation: `docs/` and `CAD/`.
- Existing archived release: https://doi.org/10.5281/zenodo.19664270.

The DOI above identifies the earlier archive. It must not be used as evidence that
this revision has already been archived. After validating and releasing this
revision, archive that release on Zenodo and cite its returned version DOI in the
manuscript and release notes.
