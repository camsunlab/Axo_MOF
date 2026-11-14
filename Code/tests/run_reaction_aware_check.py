"""Check the reaction-aware orchestrator with network connections and hardware imports blocked.

1. Seeds: the loop starts from the 5 maximin seeds of the campaign (Code/sampling.ipynb).
2. Campaign replay: the recorded Dataset/ batches are fed back to the loop in order. Every plan
   the loop writes must match the formulations, batch size and measurement interval of the batch
   that was actually run, the phase change must follow alpha(t1) > 0.5 after BO_4, and the run
   must pause once the 20 vial positions are used. Two k_n values are replaced by the ones typed
   into Code/bayesian.ipynb (see CAMPAIGN_OVERRIDES).
3. Simulated hardware: a stand-in Experiment with a stand-in Spectrometer on a synthetic ground
   truth exercises per-batch data folders, copied reference spectra, Well resets, vial-full and
   phase-change pauses, resuming from a fresh process and an interrupted batch.
"""
import argparse
import json
from pathlib import Path
import shutil

import numpy as np
import pandas as pd

import _offline_guard

DATASET = Path(__file__).resolve().parents[2] / "Dataset"
CAMPAIGN_ORDER = ["initial sampling", "BO_1", "BO-2", "BO-3", "BO_4", "BO-5", "BO-6", "BO-7", "BO-8"]
CAMPAIGN_SEEDS = [[0.4, 4.9, 2.7], [7.9, 0.1, 0.0], [0.1, 0.1, 7.8], [3.8, 0.5, 3.7], [4.3, 3.7, 0.0]]
# k_n values entered by hand in Code/bayesian.ipynb that the automatic pipeline does not reproduce:
# BO-2 B1 was fitted with its 45-min reading removed (gualtieri.ipynb cell 110), and BO-7 A3 comes
# from a rerun (BO_7_Zuyang_20251124_131926) that is not in Dataset/.
CAMPAIGN_OVERRIDES = {("BO-2", "B1"): 0.026131, ("BO-7", "A3"): 1.94775}


def _volumes(entries) -> list:
    return sorted(tuple(round(float(v), 6) for v in e) for e in entries)


def _plan_volumes(plan: dict) -> tuple[str, list]:
    slots = [k for k in plan if k.startswith("slot")]
    assert len(slots) == 1, f"A batch spans several slots: {slots}"
    return slots[0], _volumes((v["metal_precursor_vol"], v["organic_precursor_vol"], v["solvent_vol"])
                              for v in plan[slots[0]].values())


def check_seeds() -> dict:
    from science_jubilee.optimization.reaction_aware import campaign_initial_sampling
    seeds = campaign_initial_sampling().round(6).tolist()
    assert seeds == CAMPAIGN_SEEDS, seeds
    return {"seeds_mL": seeds}


class ReplayExperiment:
    """Returns the recorded batch whose recipes the plan must reproduce."""

    def __init__(self):
        self.n, self.experiment_dir, self.log = 0, None, []

    def make_batch(self, plan_path: str) -> None:
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        _, planned = _plan_volumes(plan)
        batch = next(p for p in DATASET.iterdir() if p.is_dir() and p.name.startswith(CAMPAIGN_ORDER[self.n] + "_"))
        recipes = json.loads((batch / "mof_recipes.json").read_text(encoding="utf-8"))
        recorded = _volumes([r[k]["volume_ml"] for k in ("metal_precursor", "organic_precursor", "solvent")]
                            for r in recipes.values())
        log = (batch / "operations_log.txt").read_text(encoding="utf-8")
        interval = 5 if "at 5 mins" in log else 15
        assert planned == recorded, f"{batch.name}: planned {planned}, recorded {recorded}"
        assert plan["spectrum_record_interval_mins"] == interval, (batch.name, plan["spectrum_record_interval_mins"])
        self.log.append({"batch": batch.name, "n_vials": len(planned), "interval_min": interval})
        self.experiment_dir = str(batch)
        self.n += 1


def check_campaign_replay(work: Path) -> dict:
    import science_jubilee.optimization.reaction_aware as ra
    original = ra.analyse_batch_dir

    def with_notebook_values(batch_dir):
        rows, notes = original(batch_dir)
        for row in rows:
            key = (Path(batch_dir).name.split("_")[0], row["vial_id"])
            if key in CAMPAIGN_OVERRIDES:
                row["optimization_score"] = CAMPAIGN_OVERRIDES[key]
        return rows, notes

    ra.analyse_batch_dir = with_notebook_values
    try:
        experiment = ReplayExperiment()
        loop = ra.ReactionAwareBOLoop(experiment, output_dir=work / "replay", save_plots=False)
        statuses = []
        while True:
            result = loop.run()
            statuses.append(result["status"])
            if result["status"] == "paused_vials_full":
                loop.replace_vials()
            elif result["status"] != "paused_phase_change":
                break
    finally:
        ra.analyse_batch_dir = original
    assert experiment.n == len(CAMPAIGN_ORDER), experiment.log
    assert statuses == ["paused_phase_change", "paused_vials_full", "paused_phase_change", "completed"], statuses
    decisions = pd.read_csv(work / "replay" / "decisions.csv")
    switch = decisions[decisions["next_phase"] == 2].iloc[0]
    assert switch["experiment_name"] == "P1-batch_bo-04" and switch["max_alpha_t1"] > 0.5, switch.to_dict()
    assert (decisions.loc[decisions["iteration"] < switch["iteration"], "max_alpha_t1"].fillna(0) <= 0.5).all()
    np.testing.assert_allclose(result["best_k_n"], 2.333737, rtol=1e-4)
    return {"batches": experiment.log, "pauses": statuses,
            "switch_after": switch["experiment_name"], "max_alpha_t1_at_switch": float(switch["max_alpha_t1"]),
            "best_k_n": result["best_k_n"], "overrides": {f"{b} {v}": k for (b, v), k in CAMPAIGN_OVERRIDES.items()}}


class FakeWell:
    def __init__(self):
        self.currentLiquidVolume, self.num_readings_taken, self.next_spectrum_recordtime = 0, 0, None


class FakeSpectrometer:
    """Only the attributes ReactionAwareBOLoop and Experiment touch between batches."""

    def __init__(self, base: Path):
        self.base_dir = base
        self.experiment_metadata = {"experiment_name": "BO_Optimization", "operator_name": "sim"}
        self.experiment_dir = base / "session_start"
        self.refs_dir = self.experiment_dir / "references"
        self.refs_dir.mkdir(parents=True)
        self._dark_path, self._white_path = self.refs_dir / "dark_1.npy", self.refs_dir / "white_1.npy"
        np.save(self._dark_path, np.zeros(3))
        np.save(self._white_path, np.ones(3))

    def _save_experiment_metadata(self):
        (Path(self.experiment_dir) / "experiment_metadata.json").write_text(json.dumps(self.experiment_metadata))


class FakeHardwareExperiment:
    """Stand-in for Experiment: writes into the spectrometer's folder like the real one and tracks
    which physical vial positions hold a reaction, so reusing a full vial fails the test."""

    def __init__(self, base: Path, truth, occupied: set):
        self.spectrometer = FakeSpectrometer(base)
        self.truth, self.occupied, self.crash_next, self.n = truth, occupied, False, 0
        for slot in (2, 5):
            wells = {f"{r}{c}": FakeWell() for r in "AB" for c in range(1, 6)}
            for view in ("ssy", "sy", "spec"):
                setattr(self, f"samples{slot}_{view}", wells)

    def make_batch(self, plan_path: str) -> None:
        from science_jubilee.simulation.virtual_lab import write_batch
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        slot_key, _ = _plan_volumes(plan)
        slot = int(slot_key[4:])
        out = Path(self.spectrometer.experiment_dir)
        assert out.name.startswith(plan["Experiment"]["experiment_name"]), "Batch folder was not rotated."
        assert (out / "references" / "dark_1.npy").is_file(), "Reference spectra were not copied."
        assert Path(self.log_path).parent == out
        for well in plan[slot_key]:
            assert (slot, well) not in self.occupied, f"Vial slot{slot} {well} already holds a reaction."
            assert getattr(self, f"samples{slot}_spec")[well].num_readings_taken == 0, "Well state was not reset."
        if self.crash_next:
            self.crash_next = False
            raise RuntimeError("simulated hardware fault during the batch")
        self.n += 1
        write_batch(plan, out, self.truth, seed=100 + self.n)
        for well in plan[slot_key]:
            self.occupied.add((slot, well))
            getattr(self, f"samples{slot}_spec")[well].num_readings_taken = plan["max_spectrum_records"]


def check_simulated_hardware(work: Path) -> dict:
    from science_jubilee.optimization.reaction_aware import ReactionAwareBOLoop, PHASES
    from science_jubilee.simulation.virtual_lab import GroundTruth
    out, occupied, events = work / "hardware", set(), []
    # One 10-vial rack so the run has to stop for fresh vials.
    settings = dict(output_dir=out / "results", save_plots=False, max_bo_batches=4, max_validation_batches=3,
                    vial_slots=(2,))

    def session():  # every call stands for a fresh process after the operator restarts the run
        experiment = FakeHardwareExperiment(out / f"spectra_{len(events)}", GroundTruth(), occupied)
        loop = ReactionAwareBOLoop(experiment, **settings)
        if loop.state_file.exists():
            loop.load_state()
        return loop, experiment

    loop, _ = session()
    result = loop.run()
    events.append(result["status"])
    assert result["status"] == "paused_phase_change", result
    crashed = False
    while result["status"] != "completed":
        loop, experiment = session()
        if result["status"] == "paused_vials_full":
            occupied.clear()
            loop.replace_vials()
        elif result["status"] == "paused_pending_incomplete":
            loop.discard_pending()
        if not crashed and loop.phase >= 1:
            experiment.crash_next, crashed = True, True
            try:
                loop.run()
            except RuntimeError:
                events.append("crash")
                result = {"status": "crashed"}
                continue
        result = loop.run()
        events.append(result["status"])
        assert len(events) < 30, events
    assert crashed and "paused_pending_incomplete" in events and "paused_vials_full" in events, events
    decisions = pd.read_csv(settings["output_dir"] / "decisions.csv")
    measured = decisions[decisions.get("discarded", pd.Series(False, index=decisions.index)) != True]
    for _, row in measured.iterrows():
        phase = PHASES[int(row["phase"])]
        assert row["interval_min"] == phase.interval_min and row["n_vials"] <= phase.batch_size, row.to_dict()
        if row["phase"] < 2 and row["next_phase"] == 2:
            assert row["max_alpha_t1"] > 0.5, row.to_dict()
        if row["phase"] == 1 and row["next_phase"] == 1:
            assert not row["max_alpha_t1"] > 0.5, row.to_dict()
    assert set(measured["phase"]) == {0, 1, 2}, "The run never reached the validation phase."
    folders = list(measured["batch_dir"])
    assert len(set(folders)) == len(folders), "Two batches share a data folder."
    for folder in folders:
        assert (Path(folder) / "operations_log.txt").is_file() and (Path(folder) / "references").is_dir()
    state = json.loads(loop.state_file.read_text(encoding="utf-8"))
    assert state["status"] == "completed" and state["pending_batch"] is None
    return {"events": events, "batches": len(measured), "discarded": int(len(decisions) - len(measured)),
            "phases": measured["phase"].astype(int).tolist(),
            "max_alpha_t1": [None if pd.isna(a) else round(float(a), 3) for a in measured["max_alpha_t1"]],
            "best_k_n": result["best_k_n"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", default="reaction_aware_check_outputs")
    parser.add_argument("--skip-replay", action="store_true", help="skip the campaign replay (needs Dataset/)")
    args = parser.parse_args()
    _offline_guard.install()
    work = Path(args.output)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    report = {"seeds": check_seeds()}
    if not args.skip_replay:
        report["campaign_replay"] = check_campaign_replay(work)
    report["simulated_hardware"] = check_simulated_hardware(work)
    loaded = _offline_guard.loaded_hardware_modules()
    assert not loaded, loaded
    report["hardware_modules_loaded"] = loaded
    (work / "reaction_aware_check.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    print("Reaction-aware orchestrator check passed.")


if __name__ == "__main__":
    main()
