"""Check reference outputs and physically feasible proposals for the bundled data."""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd


def verify(output: Path, expected: Path) -> dict:
    actual = pd.read_csv(output / "kinetic_parameters.csv").set_index("vial_id").sort_index()
    reference = pd.read_csv(expected / "kinetic_parameters.csv").set_index("vial_id").sort_index()
    assert actual.index.equals(reference.index), "Vial IDs differ from the reference."
    assert actual["status"].equals(reference["status"]), "Optical-screening decisions changed."
    # Parameters the recorded data do not resolve vary across platforms at equal R², so they
    # are not compared at the strict tolerance: every parameter of a fit whose a lies beyond
    # the observed window, and k_g when growth is already saturated at the first follow-up.
    assert actual["a_outside_observed_window"].equals(reference["a_outside_observed_window"]), \
        "Observation-window flags changed."
    resolved = ~reference["a_outside_observed_window"]
    columns = ["a_min", "b_min", "I_max", "k_n_min_inverse", "optimization_score", "alpha_first_followup"]
    np.testing.assert_allclose(actual.loc[resolved, columns].to_numpy(float),
                               reference.loc[resolved, columns].to_numpy(float),
                               rtol=1e-3, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(actual.loc[~resolved, columns].to_numpy(float),
                               reference.loc[~resolved, columns].to_numpy(float),
                               rtol=1e-2, atol=1e-6, equal_nan=True)
    def growth_at_first_followup(table):
        return 1 - np.exp(-(table["k_g_min_inverse"] * table["first_followup_min"]) ** 3)
    saturated = growth_at_first_followup(reference) > 1 - 1e-6
    assert (growth_at_first_followup(actual)[saturated] > 1 - 1e-6).all(), \
        "Growth term is no longer saturated at the first follow-up."
    for mask, rtol in ((resolved & ~saturated, 1e-3), (~resolved & ~saturated, 1e-2)):
        np.testing.assert_allclose(actual.loc[mask, "k_g_min_inverse"], reference.loc[mask, "k_g_min_inverse"],
                                   rtol=rtol, atol=1e-6, equal_nan=True)
    np.testing.assert_allclose(actual["R2"], reference["R2"], rtol=0, atol=1e-5, equal_nan=True)
    valid = actual["status"] == "fitted"
    np.testing.assert_allclose(actual.loc[valid, "k_n_min_inverse"], 1 / actual.loc[valid, "a_min"])
    assert (actual.loc[~valid, "optimization_score"] == 0).all()
    assert actual.loc[~valid, "k_n_min_inverse"].isna().all()
    plan = json.loads((output / "next_batch_plan.json").read_text())
    reference_plan = json.loads((expected / "next_batch_plan.json").read_text())
    for name in ["threshold", "switch_to_measurement_prioritized", "next_mode", "next_batch_size", "next_measurement_interval_min", "executed"]:
        assert plan[name] == reference_plan[name], f"Scheduling mismatch: {name}"
    assert plan["executed"] is False
    candidates = pd.read_csv(output / "next_conditions.csv")
    expected_candidates = pd.read_csv(expected / "next_conditions.csv")
    cols = ["Co_mL", "mIm_mL", "TEA_mL"]
    volumes = candidates[cols].to_numpy(float)
    assert len(candidates) == plan["next_batch_size"]
    assert (volumes >= 0).all()
    np.testing.assert_allclose(volumes.sum(axis=1), 8.0, rtol=0, atol=1e-8)
    np.testing.assert_allclose(volumes / .1, np.round(volumes / .1), rtol=0, atol=1e-8)
    assert len({tuple(row) for row in np.round(volumes, 8)}) == len(volumes)
    observed = {tuple(row) for row in np.round(actual[cols].to_numpy(float), 8)}
    assert not any(tuple(row) in observed for row in np.round(volumes, 8))
    np.testing.assert_allclose(volumes, expected_candidates[cols].to_numpy(float), rtol=0, atol=1e-8)
    for name in ["predicted_score", "predictive_std", "expected_improvement"]:
        np.testing.assert_allclose(candidates[name], expected_candidates[name], rtol=1e-3, atol=1e-6)
    from science_jubilee.offline_demo import default_data_dir, verify_input_files
    manifest = verify_input_files(default_data_dir())
    assert manifest["manifest_present"]
    for name in ["kinetic_fits.png", "gp_recommendations.png", "processed_trajectories.csv", "run_summary.json"]:
        assert (output / name).is_file() and (output / name).stat().st_size > 0
    return {"reference_numerical_values": "passed", "optical_gates": "passed",
            "kinetic_objective": "passed", "scheduling_decision": "passed",
            "proposal_constraints": "passed", "source_integrity": "passed",
            "parameter_rtol": 1e-3, "parameter_atol": 1e-6, "R2_atol": 1e-5,
            "extrapolated_fit_rtol": 1e-2, "saturated_growth_k_g": "saturation checked, value not compared"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("offline_demo_outputs"))
    parser.add_argument("--expected", type=Path, default=Path(__file__).resolve().parents[1] / "expected_offline_output")
    args = parser.parse_args()
    result = verify(args.output.resolve(), args.expected.resolve())
    print(json.dumps(result, indent=2))
    (args.output / "reference_check.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
