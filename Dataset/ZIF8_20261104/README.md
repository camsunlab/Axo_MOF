# Recorded ZIF-8 campaign, 4 November 2026

`autonomous_mof_synthesis_executed.ipynb` and `run_records/` were copied unchanged
from the experiment-computer `Code.zip`. The original absolute paths and numerical
values are retained as historical evidence. The saved state is not a portable
instruction to resume a physical experiment.

| Round | Vials | Interval (min) | Maximum alpha(t1) |
| --- | ---: | ---: | ---: |
| 1 | 5 | 15 | 0.7281483073 |
| 2 | 2 | 5 | 0.0352744638 |
| 3 | 2 | 5 | 0.0338234549 |
| 4 | 2 | 5 | 0.0718565963 |

The round-1 result triggered automatic switching. A reagent-refill pause occurred
after round 2. The configurable 1-vial/2-min level was not reached.

## Input files

The original spectrum folders are provided below `raw/`, retaining the four
folder names recorded in `run_records/decisions.csv`. Each batch includes its
per-vial CSVs, `mof_recipes.json`, `operations_log.txt`,
`experiment_metadata.json` and dark/white reference arrays.
`raw/raw_data_manifest.json` records the deposited input checksums.

The data-completeness job in [GitHub Actions run 43631912972](https://github.com/camsunlab/Axo_MOF/actions/runs/43631912972)
passed for main commit `522a439d1f0e86cae3a7822df3eabd8cb918452f`.

## Maintaining the input archive

For maintainers who need to restage the original inputs, use the copy utility
with the actual source directory on the experiment computer:

```powershell
python Code/prepare_zif8_upload.py --source "C:\path\to\original\ZIF8_batches" --output zif8_data_upload
```

The utility excludes generated PNG plots and creates a checksum manifest.
The deposited inputs are already available; this restaging step is only for
archive maintenance. To check the downloaded repository, run from its root:

```bash
python Code/prepare_zif8_upload.py --check-root .
```

This checks file completeness and integrity, not agreement of re-fitted kinetic
parameters or all manuscript figures. Do not describe the archived summaries as
a substitute for the recorded spectral inputs.

The legacy `Co_mL` column denotes the metal-precursor volume. In this ZIF-8
archive that precursor is Zn2+, as recorded in the saved reagent names. The
original column names have been retained to preserve the execution records.
