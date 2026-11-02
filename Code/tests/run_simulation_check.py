"""Check the simulation mode with network connections and hardware imports blocked."""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

import _offline_guard

BATCH_FILES = ("operations_log.txt", "mof_recipes.json", "experiment_metadata.json", "ground_truth.json")


def _plan(vials, interval=15, records=13):
    return {"slot2": {f"A{i + 1}": {"metal_precursor_vol": x[0], "organic_precursor_vol": x[1],
                                    "solvent_vol": x[2]} for i, x in enumerate(vials)},
            "spectrum_record_interval_mins": interval, "max_spectrum_records": records}


def check_round_trip(work: Path, truth) -> dict:
    """Noise-free files read back with the upstream parsers recover the true parameters."""
    from science_jubilee.analysis.gualtieri import (read_uvvis, extract_series_at_wavelength,
                                                    parse_times_from_operations_log, fit_gualtieri)
    from science_jubilee.simulation.virtual_lab import write_batch
    worst = 0.0
    for interval in (15, 5):
        out = work / f"round_trip_{interval}"
        truths = write_batch(_plan([(3.0, 2.5, 2.5), (2.0, 1.5, 4.5), (2.3, 3.2, 2.5)], interval),
                             out, truth, seed=0, noise=False)
        for vial, p in truths.items():
            wavelengths, spectra = read_uvvis(str(out / "spectra" / f"{vial}.csv"))
            nominal, signal, _ = extract_series_at_wavelength(wavelengths, spectra, 400.0)
            times = parse_times_from_operations_log(str(out / "operations_log.txt"), vial, 2)
            fit = fit_gualtieri(np.array([times[int(t)] for t in nominal]), signal)
            for name, value in (("a", fit["params"][1]), ("I_max", fit["params"][3])):
                error = abs(value - p[name]) / p[name]
                assert error < 1e-2, f"{vial} at {interval} min: fitted {name} {value} vs true {p[name]}"
                worst = max(worst, error)
    return {"max_relative_error": worst, "tolerance": 1e-2}


def check_gates(work: Path, truth) -> dict:
    from science_jubilee.offline_demo import analyse_batch
    from science_jubilee.simulation.virtual_lab import write_batch
    out = work / "gates"
    write_batch(_plan([(2.3, 3.2, 2.5), (2.1, 0.1, 5.8), (0.1, 2.5, 5.4)]), out, truth, seed=1)
    statuses = {row["vial_id"]: row["status"] for row in analyse_batch(out, progress=False)[0]}
    expected = {"A1": "fitted", "A2": "no_target_feature", "A3": "no_reaction"}
    assert statuses == expected, statuses
    return statuses


def check_upstream(work: Path, truth) -> dict:
    from science_jubilee.simulation.run_upstream import build_loop, run_upstream
    summary = run_upstream(work, truth, phases="single", batch_size=3, max_iterations=2)
    out = work / "upstream"
    assert summary["iterations_run"] == 3 and summary["n_vials"] == 11, summary
    assert (out / "bo_state.json").is_file()
    resumed = build_loop(out, truth, phases="single", batch_size=3, max_iterations=2)
    resumed.load_state()
    n_before = len(resumed.X)
    resumed.run_iteration()
    assert len(resumed.X) == n_before + 3, "Resume failed."
    assert len(list((out / "batches").glob("batch_*"))) == 4
    return {"iterations_before_resume": summary["iterations_run"], "vials_after_resume": int(len(resumed.X)),
            "true_I_max_regret": summary["true_I_max_regret"]}


def check_policy(work: Path, truth) -> dict:
    from science_jubilee.simulation.run_policy import run_policy
    summary = run_policy(work, truth, rounds=3, progress=False)
    modes = [r["mode"] for r in summary["rounds"]]
    assert "measurement-prioritized" in modes, modes
    vials = pd.read_csv(work / "policy" / "vials.csv")
    volumes = vials[["Co_mL", "mIm_mL", "TEA_mL"]].to_numpy(float)
    assert (volumes >= 0).all()
    np.testing.assert_allclose(volumes.sum(axis=1), 8.0, rtol=0, atol=1e-8)
    np.testing.assert_allclose(volumes / .1, np.round(volumes / .1), rtol=0, atol=1e-8)
    assert len({tuple(v) for v in np.round(volumes, 8)}) == len(volumes), "A composition was tested twice."
    return {"modes": modes, "true_k_n_regret": summary["rounds"][-1]["true_k_n_regret"]}


def check_determinism(work: Path, truth) -> dict:
    from science_jubilee.simulation.run_policy import run_policy
    runs = [work / "determinism_1", work / "determinism_2"]
    for out in runs:
        run_policy(out, truth, rounds=2, progress=False)
    first = runs[0] / "policy" / "batches"
    files = [p.relative_to(first) for p in sorted(first.rglob("*"))
             if p.is_file() and (p.suffix == ".csv" or p.name in BATCH_FILES)]
    assert files
    for rel in files:
        assert (first / rel).read_bytes() == (runs[1] / "policy" / "batches" / rel).read_bytes(), rel
    assert (runs[0] / "policy" / "vials.csv").read_bytes() == (runs[1] / "policy" / "vials.csv").read_bytes()
    return {"files_compared": len(files) + 1, "identical": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("simulation_check_outputs"))
    args = parser.parse_args()
    _offline_guard.install()
    from science_jubilee.simulation.virtual_lab import GroundTruth
    truth = GroundTruth()
    work = args.output.resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    result = {"round_trip": check_round_trip(work, truth), "optical_gates": check_gates(work, truth),
              "upstream_loop_and_resume": check_upstream(work, truth), "policy_loop": check_policy(work, truth),
              "determinism": check_determinism(work, truth)}
    loaded = _offline_guard.loaded_hardware_modules()
    assert not loaded, loaded
    result.update(network_connections_blocked=True, hardware_modules_loaded=loaded, simulated=True,
                  result="passed")
    (work / "simulation_check.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
