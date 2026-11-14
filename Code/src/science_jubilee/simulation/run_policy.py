"""Loop B: the k_n two-mode policy of the offline demo, repeated over simulated rounds.

Each round writes a simulated batch, analyses it with the offline demo's optical
gates, log-derived times and Gualtieri fit, applies the two-mode rule to that
batch, and proposes the next batch from all rounds so far. Every vial is assumed
to receive the same number of readings in both modes.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from science_jubilee.offline_demo import analyse_batch, mode_decision, recommend
from science_jubilee.optimization.sampling import initial_sampling
from science_jubilee.simulation.virtual_lab import GroundTruth, SimulatedExperiment
from science_jubilee.utils.synthesis_plan import create_synthesis_plan_json

COLUMNS = ["Co_mL", "mIm_mL", "TEA_mL"]


def run_policy(output: Path, truth: GroundTruth, seed=42, rounds=6, threshold=0.5,
               n_initial=5, n_records=13, progress=True) -> dict:
    out = Path(output) / "policy"
    experiment = SimulatedExperiment(out, truth, seed=seed)
    k_n_best = truth.optimum("k_n")[1]
    X_next = initial_sampling(total_ml=8.0, step_ml=0.1, n_init=n_initial, min_co=0.1,
                              min_mim=0.1, min_tea=0.0, random_state=seed)
    interval, mode = 15, "throughput-prioritized"
    X_all, y_all, vial_rows, round_rows, stop_reason = np.empty((0, 3)), np.empty(0), [], [], "rounds completed"
    for r in range(rounds):
        plan = create_synthesis_plan_json(X_next, str(out / "plans" / f"round_{r:02d}.json"),
                                          experiment_name=f"policy-round{r:02d}", operator_name="simulation",
                                          spectrum_interval_mins=interval, max_spectrum_records=n_records)
        experiment.make_batch(plan)
        batch_dir = Path(experiment.experiment_dir)
        truths = experiment.history[-1]["truth"]
        rows, _, notes = analyse_batch(batch_dir, progress=False)
        decision = mode_decision(rows, threshold)
        X = np.array([[row[c] for c in COLUMNS] for row in rows])
        y = np.array([row["optimization_score"] for row in rows])
        X_all, y_all = np.vstack([X_all, X]), np.concatenate([y_all, y])
        for row in rows:
            vial_rows.append({"round": r, "measurement_interval_min": interval, **row,
                              "true_a_min": truths[row["vial_id"]]["a"],
                              "true_k_n_min_inverse": truths[row["vial_id"]]["k_n"]})
        best = int(np.argmax(y_all))
        best_true = truth.true_k_n(X_all[best])
        round_rows.append({"round": r, "mode": mode, "batch_size": len(rows),
                           "measurement_interval_min": interval, "batch_dir": batch_dir.name,
                           "statuses": {row["vial_id"]: row["status"] for row in rows},
                           "max_alpha_first_followup": decision["max_alpha_first_followup"],
                           "next_mode": decision["next_mode"],
                           "best_measured_composition_mL": X_all[best].tolist(),
                           "best_measured_score": float(y_all[best]),
                           "best_measured_true_k_n": best_true,
                           "true_k_n_regret": k_n_best - best_true, "warnings": notes})
        if progress:
            print(f"[policy] round {r}: {len(rows)} vials at {interval} min; "
                  f"best measured k_n {y_all[best]:.4f} (true {best_true:.4f} of {k_n_best:.4f}); "
                  f"next: {decision['next_mode']}", flush=True)
        if r == rounds - 1:
            break
        try:
            proposals, *_ = recommend(X_all, y_all, decision["next_batch_size"], seed)
        except RuntimeError as exc:
            stop_reason = f"stopped after round {r}: {exc}"
            break
        X_next = proposals[COLUMNS].to_numpy()
        interval, mode = decision["next_measurement_interval_min"], decision["next_mode"]
    pd.DataFrame(vial_rows).to_csv(out / "vials.csv", index=False)
    summary = {"loop": "k_n two-mode policy (offline-demo analysis, multi-round)",
               "objective": "k_n = 1/a with optical gates; gate failures score 0",
               "threshold": threshold, "readings_per_vial": n_records, "n_initial": n_initial,
               "rounds_run": len(round_rows), "n_vials": len(vial_rows), "stop_reason": stop_reason,
               "true_k_n_optimum": k_n_best, "rounds": round_rows}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
