"""Loop A: the unchanged upstream BayesianOptimizationLoop driven by the simulated lab.

Only the operator prompt for loading vials is replaced; planning, I_max
extraction (batch_extract_yields), GP fitting, EI selection, phase transitions,
convergence checks and bo_state.json persistence are the upstream code.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np

from science_jubilee.optimization.orchestrator import BayesianOptimizationLoop
from science_jubilee.optimization.phase_config import DEFAULT_PHASES, create_single_phase_config
from science_jubilee.simulation.virtual_lab import GroundTruth, SimulatedExperiment
from science_jubilee.utils.synthesis_plan import get_vial_assignments_from_plan


class SimulatedBOLoop(BayesianOptimizationLoop):
    """Upstream loop with the vial-loading prompt removed and a safety cap on iterations."""

    def __init__(self, *args, max_total_iterations: int | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_total_iterations = max_total_iterations
        self.stopped_by_cap = False
        self.iteration_summaries: list[dict] = []

    def wait_for_vial_loading(self, plan_path: str):
        self.logger.info(f"Simulation: vials loaded virtually: {get_vial_assignments_from_plan(plan_path)}")

    def run_iteration(self):
        if self.max_total_iterations is not None and self.iteration >= self.max_total_iterations:
            self.stopped_by_cap = True
            # The upstream run() treats KeyboardInterrupt as an operator stop: it saves state and returns.
            raise KeyboardInterrupt(f"simulation cap of {self.max_total_iterations} iterations reached")
        summary = super().run_iteration()
        self.iteration_summaries.append(summary)
        return summary


def build_loop(out: Path, truth: GroundTruth, seed=42, phases="multi", batch_size=5,
               max_iterations=4, max_total_iterations=25) -> SimulatedBOLoop:
    phase_list = (copy.deepcopy(DEFAULT_PHASES) if phases == "multi"
                  else create_single_phase_config(batch_size=batch_size, max_iterations=max_iterations, xi=0.01))
    return SimulatedBOLoop(machine=None, experiment=SimulatedExperiment(out, truth, seed=seed),
                           phases=phase_list, approval_mode="automatic", output_dir=str(out),
                           n_initial_samples=5, convergence_threshold=0.001, state_file="bo_state.json",
                           operator_name="simulation", max_total_iterations=max_total_iterations)


def run_upstream(output: Path, truth: GroundTruth, seed=42, phases="multi", batch_size=5,
                 max_iterations=4, max_total_iterations=25) -> dict:
    out = Path(output) / "upstream"
    loop = build_loop(out, truth, seed, phases, batch_size, max_iterations, max_total_iterations)
    loop.run()
    imax_best = truth.optimum("I_max")[1]
    best = int(np.argmax(loop.y))
    true_imax = [truth.parameters(x)["I_max"] for x in loop.X]
    iterations = []
    for s, batch in zip(loop.iteration_summaries, loop.experiment.history):
        iterations.append({**{k: s[k] for k in ("iteration", "phase_name", "phase_iteration", "phase_transitioned",
                                                "n_samples_total", "best_yield", "ei_max", "converged", "reason")},
                           "batch_dir": Path(batch["dir"]).name, "batch_size": len(batch["truth"]),
                           "best_true_I_max_so_far": max(true_imax[:s["n_samples_total"]])})
    summary = {"loop": "upstream BayesianOptimizationLoop (unchanged), simulated experiment",
               "objective": "I_max from upstream batch_extract_yields; no optical gates; failed fits score 0",
               "phases": phases, "iterations_run": loop.iteration, "n_vials": int(len(loop.X)),
               "converged": loop.converged, "convergence_reason": loop.convergence_reason,
               "stopped_by_cap": loop.stopped_by_cap, "max_total_iterations": max_total_iterations,
               "true_I_max_optimum": imax_best,
               "best_measured_composition_mL": loop.X[best].tolist(), "best_measured_I_max": float(loop.y[best]),
               "best_measured_true_I_max": true_imax[best], "true_I_max_regret": imax_best - true_imax[best],
               "iterations": iterations}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
