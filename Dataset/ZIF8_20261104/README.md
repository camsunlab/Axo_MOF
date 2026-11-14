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

The experiment-computer Code folder did not contain the original spectrum folders.
They are expected below `raw/`, retaining the four folder names recorded in
`run_records/decisions.csv`. Each batch needs its per-vial CSVs, `mof_recipes.json`,
`operations_log.txt`, `experiment_metadata.json` and dark/white reference arrays.

To copy these inputs from the experiment computer without altering them:

```powershell
python Code/prepare_zif8_upload.py --source "C:\Users\amg-admin\Desktop\Axo\Axo_MOF\spectrum_data\2026-11-04\Zuyang" --output zif8_data_upload
```

Upload the generated `zif8_data_upload/Dataset/` folder into the repository root.
The utility excludes generated PNG plots and creates a checksum manifest.
After uploading, check the actual deposited content with:

```bash
python Code/prepare_zif8_upload.py --check-root .
```

This checks file completeness and integrity, not agreement of re-fitted kinetic
parameters or all manuscript figures. Do not describe the archived summaries as
a substitute for the recorded spectral inputs.

The legacy `Co_mL` column denotes the metal-precursor volume. In this ZIF-8
archive that precursor is Zn2+, as recorded in the saved reagent names. The
original column names have been retained to preserve the execution records.
