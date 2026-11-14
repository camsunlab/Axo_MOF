# Code-availability revision: provenance and scope

## Sources

- Development base: `670bfb9a197e57f6bdc5dfb48caa50cc74ecdbd1` on `reaction-aware-orchestrator`.
- Main branch checked: `ad12c0f3128cd682d23432d7000e0399b6591ee9`.
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

The local validation record is in `revision_validation.json`. GitHub Actions
also runs the software checks on the actual revision branch, pull-request merge
and resulting main commit. Its second job requires the four ZIF-8 input folders
and their checksum manifest. Until those inputs are supplied, that job must fail
rather than treating summary tables as complete raw data.

The local environment cannot start a separate Jupyter kernel because its socket
operations are restricted. The integrated notebook is therefore tested with
`--in-process`, which executes its prepared cells in IPython. This does not claim
validation of the Jupyter browser interface or a fresh live-hardware experiment.

## Remaining scientific reproducibility work

The supplied Code folder does not contain the ZIF-8 spectral input files. The
author must upload those from the experiment computer using the documented
unchanged-copy utility and confirm the archive check succeeds.

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

After the input files and tests are complete, merge the revision into main,
create a release from that commit, archive the release as a new Zenodo version
and use its returned version DOI. The older DOI `10.5281/zenodo.19664270` does not
establish that this revision has already been archived.
