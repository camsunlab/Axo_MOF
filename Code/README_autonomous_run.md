# Integrated Axo hardware workflow

`autonomous_mof_synthesis.ipynb` is the public entry point for the integrated
workflow used for the recorded ZIF-8 demonstration. The original executed notebook
is retained in `../Dataset/ZIF8_20261104/`.

## Environments

The recorded hardware notebook used Python 3.11.16 and an installed OceanDirect
SDK. Keep that working hardware environment for the existing apparatus. The
Python 3.12 lockfile in `README_offline.md` is for validated offline examples; do
not replace a working instrument environment simply to run an offline example.

The notebook adds its adjacent `src/` directory to the import path. It can be
opened from the repository root or `Code/`. Importing hardware modules requires
the vendor's `oceandirect` Python SDK and compatible native libraries, obtained
from the vendor or the laboratory's authorized installation. They are not bundled.

## Configuration

Review the configuration and hardware cells before connecting to the apparatus.

| Setting | Recorded ZIF-8 configuration |
| --- | --- |
| Chemistry | Zn2+ / 2-methylimidazole / triethylamine in methanol |
| Objective | `k_n = 1/a` from the existing Gualtieri analysis |
| Signal gate | 400-nm maximum and range at least 0.12 a.u. |
| Second optical gate | `FEATURE_GATE_NM = None`; use 590.0 only for the ZIF-67 configuration |
| Transition threshold | `ALPHA_THRESHOLD = 0.5` |
| Round limit | 4 measured rounds |
| Automatic mode changes | `pause_on_phase_change=False` |
| Instrument address | Set `JUBILEE_IP` for the local Duet controller |
| Output directories | Set `SPECTRUM_BASE_DIR` and a fresh `OUTPUT_DIR` |

The level table is retained from the executed notebook:

| Level | Vials | Interval | Readings | Final nominal reading |
| --- | ---: | ---: | ---: | ---: |
| Seed / throughput | 5 | 15 min | 5 | 60 min |
| Measurement | 2 | 5 min | 13 | 60 min |
| Additional configurable level | 1 | 2 min | 13 | 24 min |

Any fitted vial with alpha(t1) greater than 0.5 advances the next round by the
configured transition. Otherwise the current measurement level is retained.
The additional 1-vial level was not reached in the recorded ZIF-8 experiment.

## Hardware preparation and execution

1. Use the actual apparatus calibration for deck coordinates, syringe conversion,
   probe position and tool offsets. Included coordinates describe the recorded
   installation and are not transferable calibration values.
2. Prepare the vial racks in slots 2 and 5, precursor/solvent reservoirs and the
   four lids in the slot-4 stack as described by the notebook and build documents.
3. Set the actual reagent volumes in `REAGENTS_ML`; the loop checks these before
   each batch. Start with the syringe and lid state expected by the notebook.
4. Run the setup cells in order, then the `loop.run()` cell. Hardware initialization
   includes the labware offset choices and the first dark/white reference prompts.
5. Inspect the returned status. `completed` means the configured round limit was
   reached. A reagent pause requires an actual refill, followed by a matching
   update of the controller's reservoir accounting and another call to `loop.run()`.

The operator-actions cell is disabled by default. Set `REFILL_REAGENTS_ML` only
after physically refilling the reservoirs. Merely running the cell does not add
liquid. `DISCARD = True` is only for an intentionally discarded pending batch.

For a kernel restart, use the existing hardware recovery procedure: make syringe,
lid and vial state consistent with the saved state before setting `RESUME = True`.
Do not resume the archived `bo_state.json` on a new physical experiment.

## Outputs and evidence

Each round writes a plan and separate spectrum folder. `OUTPUT_DIR` contains
`decisions.csv`, `vials.csv`, `bo_state.json`, `bo_optimization.log` and BO plots.
The recorded run changed from 5/15 to 2/5 automatically after round 1; it paused
for a reagent refill after round 2. The exported notebook and logs are retained
without changing their historical settings or reported values.

## Offline notebook test

From the repository root, using the Python 3.12 offline environment:

```bash
python Code/tests/notebook_dry_run.py Code/autonomous_mof_synthesis.ipynb notebook_dry_run_outputs --truth fast --in-process
```

This executes the notebook with synthetic spectra, substituted device objects,
an accelerated clock and simulated setup/refill inputs. It writes an executed
notebook and `dry_run_report.json`. It is a software test rather than a rerun of
the physical ZIF-8 campaign.

The `--in-process` option runs the same prepared notebook cells with IPython
without opening a Jupyter kernel socket. Omit it to use the separate-kernel
NotebookClient backend. Neither mode connects to laboratory hardware.
