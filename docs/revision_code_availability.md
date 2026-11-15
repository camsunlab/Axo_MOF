# Code-availability revision: provenance and scope

## Sources

- Development base: `670bfb9a197e57f6bdc5dfb48caa50cc74ecdbd1` on `reaction-aware-orchestrator`.
- Main branch checked before integration: `ad12c0f3128cd682d23432d7000e0399b6591ee9`.
- ZIF-8 hardware source and records: experiment-computer `Code.zip` supplied by the author.
- Input-file hashes and publication edits: `zif8_code_provenance.json`.

The actual `Experiment.py`, `reaction_aware.py`, `offline_demo.py`, Gualtieri module
and four updated calibration files are copied without changing their contents.
The development branch's package metadata, pinned environment, bundled ZIF-67
example and simulation are retained.

The public `autonomous_mof_synthesis.ipynb` has relative paths, a consistent
spectrometer base-directory setting, cleared cell output and an opt-in refill
accounting cell. Its chemistry, analysis settings, phase table and round limit
are retained. The executed original and saved run records remain unchanged in
`Dataset/ZIF8_20261104/`.

## Published revision status

The revision was merged into `main` at commit `522a439d1f0e86cae3a7822df3eabd8cb918452f`.
[GitHub Actions run 43631912972](https://github.com/camsunlab/Axo_MOF/actions/runs/43631912972) completed successfully for that commit.
The software-examples job checked the pinned installation, recorded-data
reference results, synthetic optimization and the integrated notebook with
simulated devices. The data job checked the four deposited ZIF-8 batches
and their checksum manifest under `Dataset/ZIF8_20261104/raw/`.

The selected ZIF-67 fixed-execution batches and ZIF-8 benchmark are deposited
under `Dataset/ZIF67_fixed_execution/raw/` and `Dataset/ZIF8_benchmark/raw/`.
The ZIF-8 checksum job covers the four-round campaign; it does not by itself
validate those additional datasets or the manuscript conclusions.

## What the evidence supports

| Evidence | Supported scope |
| --- | --- |
| Pinned Python 3.12 environment and recorded-data example | A reproducible one-batch ZIF-67 analysis and recommendation workflow without hardware or vendor drivers |
| Synthetic multi-round checks | Software planning, fitting, BO, scheduling, resume behavior and deterministic simulated outputs |
| Integrated notebook dry run | Execution of the published notebook with simulated devices and spectra; no physical experiment |
| Original ZIF-8 code, decisions and logs | Automatic change after round 1 from 5 vials/15 min to 2 vials/5 min; four rounds and eleven vials, with one reagent-refill pause after round 2 |
| Raw-data upload checker | Presence and checksum consistency of the original inputs; not validation of all scientific conclusions |

The recorded hardware notebook reports Python 3.11.16. Do not describe the
historical hardware run as having used the offline Python 3.12 environment.

## Tests for this revision

The pre-publication local validation record is retained unchanged in
`revision_validation.json`. Its pending-upload and pending-remote-validation
notes describe that earlier stage. The successful main-commit run linked
above records the subsequent software and deposited-data checks. Future
pull requests and main-branch changes rerun the configured checks.

The local environment cannot start a separate Jupyter kernel because its socket
operations are restricted. The integrated notebook is therefore tested with
`--in-process`, which executes its prepared cells in IPython. This does not claim
validation of the Jupyter browser interface or a fresh live-hardware experiment.

## Remaining scientific reproducibility work

The historical `run_reaction_aware_check.py` replay still overrides two recorded
optimization scores: BO-2 B1 and BO-7 A3. Its comments identify the manually
excluded BO-2 time point and the missing BO-7 rerun folder. These overrides have
not been removed or presented as direct raw-data reproduction. Supply the actual
BO-7 rerun inputs and document the BO-2 processing before making that claim.

The offline examples and integrated notebook do not by themselves reproduce all
main-text and supplementary figures. Deposit the actual final analysis scripts,
input datasets and figure-to-command mapping for the manuscript's quantitative
results. The historical gap report is retained with its original scope; it is
not a current completion certificate.

## Release

Create a release from the tested main commit after final repository cleanup,
archive that release as a new Zenodo version and cite its returned version
DOI. The older DOI `10.5281/zenodo.19664270` identifies the earlier archive;
the successful GitHub checks do not establish that this revision has been
archived on Zenodo.
