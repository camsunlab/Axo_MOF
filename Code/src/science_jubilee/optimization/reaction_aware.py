"""Reaction-aware batch Bayesian optimization, as described in the manuscript.

Phases
------
0  initial sampling : the 5 maximin seed formulations of Code/sampling.ipynb, 15-min interval.
1  batch BO         : 5 vials per batch, 15-min measurement interval.
2  validation       : 2 vials per batch, 5-min measurement interval. Entered (one way) as soon as
                      any fitted vial of the latest batch has an extent of reaction alpha(t1) > 0.5
                      at its first follow-up reading.

Every batch is scored as in Code/gualtieri.ipynb: 400/590-nm optical gates, then a Gualtieri fit
at 400 nm with elapsed times from operations_log.txt; the objective is k_n = 1/a and gate failures
score 0. The next batch is chosen as in Code/bayesian.ipynb: GP + EI on the 8-mL / 0.1-mL lattice,
xi = 0.01, exclusion radius 0.2, already-tested formulations excluded.

The loop saves its state and stops at every phase change and whenever no deck slot has enough
unused vial positions left, so the operator can check the run or replace vials and resume.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.spatial.distance import cdist

from science_jubilee.offline_demo import _analyse_vial, mode_decision
from science_jubilee.optimization.bayesian import select_and_plot_next_batch
from science_jubilee.optimization.logging_config import setup_bo_logger


@dataclass(frozen=True)
class Phase:
    name: str
    batch_size: int
    interval_min: int


PHASES = {0: Phase("initial_sampling", 5, 15),
          1: Phase("batch_bo", 5, 15),
          2: Phase("validation", 2, 5)}
ALPHA_THRESHOLD = 0.5
XI = 0.01
RADIUS_UC = 0.2
N_RECORDS = 13
TOTAL_ML = 8.0
STEP_ML = 0.1
BOUNDS = [(0.0, TOTAL_ML)] * 3
COLUMNS = ("Co_mL", "mIm_mL", "TEA_mL")
# Two 10-vial racks (wells A1-A5, B1-B5) on deck slots 2 and 5.
VIAL_SLOTS = (2, 5)
VIAL_WELLS = tuple(f"{row}{col}" for row in "AB" for col in range(1, 6))


class OperatorActionRequired(RuntimeError):
    """The loop cannot continue without the operator (state has been saved)."""


def campaign_initial_sampling(n_init=5, total_ml=TOTAL_ML, step_ml=STEP_ML, random_state=42) -> np.ndarray:
    """Maximin seeds exactly as in Code/sampling.ipynb (Co > 0, 2-mIm > 0, TEA >= 0).

    optimization/sampling.initial_sampling draws its random start with the legacy NumPy RNG and
    therefore returns different seeds; the campaign used this notebook version.
    """
    units = int(round(total_ml / step_ml))
    grid = np.array([(a, b, units - a - b) for a in range(1, units)
                     for b in range(1, units - a + 1)], dtype=float) * step_ml
    scaled = grid / total_ml
    rng = np.random.default_rng(random_state)
    selected = [int(rng.integers(0, len(scaled)))]
    for _ in range(n_init - 1):
        selected.append(int(np.argmax(cdist(scaled, scaled[selected]).min(axis=1))))
    return grid[selected]


class VialRack:
    """Bookkeeping of which vial positions on the deck already hold a reaction.

    A batch is always placed inside one slot: Experiment.nearest_spectrum_reading returns
    from inside its slot loop, so a batch spread over two slots would never have its second
    slot measured after t0.
    """

    def __init__(self, slots=VIAL_SLOTS, wells=VIAL_WELLS, used=None):
        self.slots, self.wells = tuple(int(s) for s in slots), tuple(wells)
        self.used = {s: set((used or {}).get(str(s), ())) for s in self.slots}

    def free(self, slot: int) -> list[str]:
        return [w for w in self.wells if w not in self.used[slot]]

    def n_free(self) -> int:
        return sum(len(self.free(s)) for s in self.slots)

    def allocate(self, k: int) -> tuple[int, list[str]] | None:
        for slot in self.slots:
            free = self.free(slot)
            if len(free) >= k:
                return slot, free[:k]
        return None

    def mark_used(self, slot: int, wells) -> None:
        self.used[int(slot)].update(wells)

    def replace(self, slots=None) -> None:
        for slot in (self.slots if slots is None else [int(s) for s in slots]):
            self.used[slot] = set()

    def state(self) -> dict:
        return {str(s): sorted(self.used[s]) for s in self.slots}


def write_plan(path: Path, X: np.ndarray, slot: int, wells: list[str], interval_min: int,
               experiment_name: str, operator_name: str, description: str) -> str:
    """Synthesis plan in the schema Experiment.make_batch reads (see Code/draft_synthesis_plan.json)."""
    now = datetime.now()
    plan = {f"slot{slot}": {well: {"metal_precursor_vol": float(x[0]), "organic_precursor_vol": float(x[1]),
                                   "solvent_vol": float(x[2])} for well, x in zip(wells, X)},
            "spectrum_record_interval_mins": int(interval_min),
            "max_spectrum_records": N_RECORDS,
            "Experiment": {"operator_name": operator_name, "experiment_name": experiment_name,
                           "experiment_date": now.strftime("%d %B %Y"), "experiment_time": now.strftime("%H:%M"),
                           "experiment_description": description,
                           "approximate_time": f"{N_RECORDS * interval_min / 60:.1f} hours",
                           "metal_precursor_name": "Co+2", "organic_precursor_name": "2-methylimidazole",
                           "solvent_name": "triethylamine/methanol"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=4) + "\n", encoding="utf-8")
    return str(path)


def analyse_batch_dir(batch_dir: Path) -> tuple[list[dict], list[str]]:
    """Gates + Gualtieri fit for every vial; a vial whose fit fails scores 0 instead of halting the run."""
    recipes = json.loads((batch_dir / "mof_recipes.json").read_text(encoding="utf-8"))
    paths = sorted((batch_dir / "spectra").glob("*.csv"))
    if not paths or {p.stem for p in paths} != set(recipes):
        raise ValueError(f"{batch_dir}: every recorded recipe must have exactly one spectral CSV.")
    rows, notes = [], []
    for path in paths:
        try:
            row, _, warnings = _analyse_vial(path, batch_dir / "operations_log.txt", recipes[path.stem])
        except RuntimeError as exc:
            volume = [float(recipes[path.stem][key]["volume_ml"])
                      for key in ("metal_precursor", "organic_precursor", "solvent")]
            row = dict(zip(("vial_id",) + COLUMNS, [path.stem, *volume]), status="fit_failed",
                       optimization_score=0.0, k_n_min_inverse=None, alpha_first_followup=None)
            warnings = [str(exc)]
        rows.append(row)
        notes.extend(f"{path.stem}: {w}" for w in warnings)
    return rows, notes


class ReactionAwareBOLoop:
    """Closed loop on the Axo platform following the manuscript's reaction-aware policy.

    ``experiment`` is a science_jubilee Experiment (or a duck-typed stand-in with
    ``make_batch(plan_path)`` that sets ``experiment_dir``, such as simulation.SimulatedExperiment).
    ``run()`` returns a status dict; any status starting with ``paused`` means the state was saved
    and the operator should act, then call ``run()`` again (or ``--resume`` from the CLI).
    """

    def __init__(self, experiment, output_dir="optimization_results", operator_name="Operator",
                 n_initial_samples=5, alpha_threshold=ALPHA_THRESHOLD, max_bo_batches=10,
                 max_validation_batches=4, max_total_vials=None, pause_on_phase_change=True,
                 vial_slots=VIAL_SLOTS, seed=42, state_file="bo_state.json", save_plots=True):
        self.experiment = experiment
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.output_dir / state_file
        self.operator_name = operator_name
        self.n_initial_samples = int(n_initial_samples)
        self.alpha_threshold = float(alpha_threshold)
        self.max_bo_batches = int(max_bo_batches)
        self.max_validation_batches = int(max_validation_batches)
        self.max_total_vials = max_total_vials
        self.pause_on_phase_change = pause_on_phase_change
        self.seed = int(seed)
        self.save_plots = save_plots
        self.logger = setup_bo_logger(str(self.output_dir))
        self.rack = VialRack(vial_slots)
        self.X, self.y = np.empty((0, 3)), np.empty(0)
        self.phase, self.iteration = 0, 0
        self.phase_batches = {p: 0 for p in PHASES}
        self.history: list[dict] = []
        self.pending: dict | None = None
        self.status = "new"
        self.session_batches = 0

    # ------------------------------------------------------------------ state
    def save_state(self) -> None:
        state = {"policy": "reaction-aware", "phase": self.phase, "phase_name": PHASES[self.phase].name,
                 "iteration": self.iteration, "phase_batches": {str(p): n for p, n in self.phase_batches.items()},
                 "X": self.X.tolist(), "y": self.y.tolist(), "vials_used": self.rack.state(),
                 "pending_batch": self.pending, "history": self.history, "status": self.status,
                 "settings": {"alpha_threshold": self.alpha_threshold, "max_bo_batches": self.max_bo_batches,
                              "max_validation_batches": self.max_validation_batches,
                              "max_total_vials": self.max_total_vials, "seed": self.seed,
                              "xi": XI, "radius_uc": RADIUS_UC, "readings_per_vial": N_RECORDS,
                              "phases": {str(p): vars(ph) for p, ph in PHASES.items()}},
                 "saved_at": datetime.now().isoformat()}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.state_file)
        if self.history:
            pd.DataFrame(self.history).to_csv(self.output_dir / "decisions.csv", index=False)

    def load_state(self) -> None:
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        if state.get("policy") != "reaction-aware":
            raise ValueError(f"{self.state_file} was not written by the reaction-aware loop.")
        self.phase, self.iteration = int(state["phase"]), int(state["iteration"])
        self.phase_batches = {int(p): int(n) for p, n in state["phase_batches"].items()}
        self.X = np.array(state["X"], float).reshape(-1, 3)
        self.y = np.array(state["y"], float)
        self.rack = VialRack(self.rack.slots, used=state["vials_used"])
        self.pending, self.history, self.status = state["pending_batch"], state["history"], state["status"]
        self.logger.info(f"Resumed at iteration {self.iteration}, phase {self.phase} ({PHASES[self.phase].name}); "
                         f"{self.rack.n_free()} unused vial positions.")

    # --------------------------------------------------------- operator actions
    def replace_vials(self, slots=None) -> None:
        """Record that the operator put fresh, empty vials in the given slots (default: all)."""
        self.rack.replace(slots)
        self.logger.info(f"Vials replaced in slot(s) {slots or list(self.rack.slots)}.")
        self.save_state()

    def discard_pending(self) -> None:
        """Drop an interrupted batch; its vial positions stay marked as used."""
        if self.pending:
            self.logger.warning(f"Discarding pending batch {self.pending['experiment_name']}.")
            self.history.append({"iteration": self.pending["iteration"], "phase": self.pending["phase"],
                                 "experiment_name": self.pending["experiment_name"], "discarded": True})
            self.pending = None
            self.save_state()

    # --------------------------------------------------------------- the loop
    def run(self, max_batches: int | None = None) -> dict:
        """Run batches until completion or a pause. ``max_batches`` caps batches in this call."""
        if self.pending:
            if self.pending["status"] != "measured":
                return self._pause("paused_pending_incomplete",
                                   f"Batch {self.pending['experiment_name']} was interrupted before all readings "
                                   "were taken. Inspect it, then resume with --discard-pending.")
            outcome = self._finish_batch()
            if outcome:
                return outcome
        done = 0
        while max_batches is None or done < max_batches:
            outcome = self._run_batch()
            done += 1
            if outcome:
                return outcome
        return {"status": "batch_cap_reached", "message": f"Stopped after {done} batch(es) in this call."}

    def _run_batch(self) -> dict | None:
        phase = PHASES[self.phase]
        if self.max_total_vials is not None and len(self.X) >= self.max_total_vials:
            return self._complete(f"vial budget of {self.max_total_vials} reached")
        k = phase.batch_size if self.phase else self.n_initial_samples
        if self.max_total_vials is not None:
            k = min(k, self.max_total_vials - len(self.X))
        placement = self.rack.allocate(k)
        if placement is None:
            free = {s: len(self.rack.free(s)) for s in self.rack.slots}
            return self._pause("paused_vials_full",
                               f"No slot has {k} unused vial positions (unused per slot: {free}). Replace the "
                               "used vials with empty ones and resume with --vials-replaced.")
        if self.phase == 0:
            X_next, ei_max = campaign_initial_sampling(k, random_state=self.seed), float("nan")
        else:
            X_next, ei_max = self._select(k)
            if len(X_next) == 0:
                return self._complete("no formulation with positive expected improvement is left")
            placement = (placement[0], placement[1][:len(X_next)])
        slot, wells = placement
        self.iteration += 1
        name = f"P{self.phase}-{phase.name}-{self.phase_batches[self.phase] + 1:02d}"
        plan = write_plan(self.output_dir / "plans" / f"batch_{self.iteration:02d}_{name}.json", X_next, slot,
                          wells, phase.interval_min, name, self.operator_name,
                          f"Reaction-aware BO, phase {self.phase} ({phase.name}), {len(X_next)} vials")
        self.rack.mark_used(slot, wells)
        self.pending = {"iteration": self.iteration, "phase": self.phase, "experiment_name": name,
                        "plan": plan, "slot": slot, "wells": wells, "X": X_next.tolist(),
                        "interval_min": phase.interval_min, "ei_max": ei_max, "status": "running",
                        "batch_dir": None, "started_at": datetime.now().isoformat()}
        self.status = "running"
        self.save_state()
        self.logger.info(f"Batch {self.iteration} ({name}): {len(X_next)} vials in slot {slot} {wells}, "
                         f"{phase.interval_min}-min interval x {N_RECORDS} readings.")
        self._prepare_hardware(name, slot, wells)
        self.experiment.make_batch(plan)
        self.session_batches += 1
        self.pending.update(status="measured", batch_dir=str(self.experiment.experiment_dir),
                            finished_at=datetime.now().isoformat())
        self.save_state()
        return self._finish_batch()

    def _select(self, k: int) -> tuple[np.ndarray, float]:
        plot = None
        if self.save_plots:
            (self.output_dir / "plots").mkdir(exist_ok=True)
            plot = str(self.output_dir / "plots" / f"batch_{self.iteration + 1:02d}_ei.png")
        X_next, _, cache = select_and_plot_next_batch(self.X, self.y, BOUNDS, k=k, xi=XI, total_ml=TOTAL_ML,
                                                      step_ml=STEP_ML, radius_uc=RADIUS_UC,
                                                      random_state=self.seed, save_plot=plot)
        plt.close("all")
        return X_next, float(np.max(cache[2][3]))

    def _finish_batch(self) -> dict | None:
        """Score the measured batch, apply the alpha(t1) rule and decide whether to stop or pause."""
        pending = self.pending
        batch_dir = Path(pending["batch_dir"])
        try:
            rows, notes = analyse_batch_dir(batch_dir)
        except Exception as exc:
            return self._pause("paused_analysis_failed",
                               f"Could not analyse {batch_dir}: {exc}. Fix the data, then resume "
                               "(or resume with --discard-pending).")
        planned = {tuple(np.round(x, 6)) for x in pending["X"]}
        measured = {tuple(np.round([r[c] for c in COLUMNS], 6)) for r in rows}
        if planned != measured:
            return self._pause("paused_analysis_failed",
                               f"{batch_dir}: recorded recipes do not match the plan {pending['plan']}.")
        X = np.array([[r[c] for c in COLUMNS] for r in rows], float)
        y = np.array([r["optimization_score"] for r in rows], float)
        self.X, self.y = np.vstack([self.X, X]), np.concatenate([self.y, y])
        decision = mode_decision(rows, self.alpha_threshold)
        old = self.phase
        if old == 0:
            self.phase = 2 if decision["switch_to_measurement_prioritized"] else 1
        elif old == 1 and decision["switch_to_measurement_prioritized"]:
            self.phase = 2
        self.phase_batches[old] += 1
        best = int(np.argmax(self.y))
        self.history.append({
            "iteration": pending["iteration"], "phase": old, "phase_name": PHASES[old].name,
            "experiment_name": pending["experiment_name"], "batch_dir": str(batch_dir),
            "slot": pending["slot"], "wells": " ".join(pending["wells"]), "n_vials": len(rows),
            "interval_min": pending["interval_min"], "ei_max": pending["ei_max"],
            "max_alpha_t1": decision["max_alpha_first_followup"], "alpha_threshold": self.alpha_threshold,
            "next_phase": self.phase, "next_phase_name": PHASES[self.phase].name,
            "k_n_batch": " ".join(f"{r['vial_id']}={r['optimization_score']:.4g}({r['status']})" for r in rows),
            "best_k_n_so_far": float(self.y[best]), "best_composition_mL": " / ".join(f"{v:.1f}" for v in self.X[best]),
            "warnings": " | ".join(notes)})
        vials = pd.DataFrame([{"iteration": pending["iteration"], "phase": old, "batch": pending["experiment_name"],
                               "slot": pending["slot"], **r} for r in rows])
        vials_csv = self.output_dir / "vials.csv"
        vials.to_csv(vials_csv, mode="a", header=not vials_csv.exists(), index=False)
        self.pending = None
        alpha = decision["max_alpha_first_followup"]
        self.logger.info(f"Batch {pending['iteration']}: k_n {[round(v, 4) for v in y]}, max alpha(t1) "
                         f"{'n/a' if alpha is None else round(alpha, 3)}; best k_n so far {self.y[best]:.4f}.")
        if self.phase == 1 and self.phase_batches[1] >= self.max_bo_batches:
            return self._complete(f"{self.max_bo_batches} batch-BO batches without alpha(t1) > {self.alpha_threshold}")
        if self.phase == 2 and self.phase_batches[2] >= self.max_validation_batches:
            return self._complete(f"{self.max_validation_batches} validation batches completed")
        if self.phase != old:
            message = (f"Phase {old} ({PHASES[old].name}) -> phase {self.phase} ({PHASES[self.phase].name}); "
                       f"max alpha(t1) = {'n/a' if alpha is None else f'{alpha:.3f}'} "
                       f"(threshold {self.alpha_threshold}). Next: {PHASES[self.phase].batch_size} vials per "
                       f"batch at {PHASES[self.phase].interval_min}-min intervals.")
            self.logger.info(message)
            if self.pause_on_phase_change:
                return self._pause("paused_phase_change", message + " Resume to continue.")
        self.status = "running"
        self.save_state()
        return None

    # ---------------------------------------------------------------- helpers
    def _prepare_hardware(self, name: str, slot: int, wells: list[str]) -> None:
        """Give each batch its own data folder and reset the Well objects of fresh vial positions.

        Upstream Experiment writes everything into the folder its Spectrometer created at
        construction; the campaign got one folder per batch by re-creating the tools in
        mof_synthesis.ipynb. Reference spectra are recorded once per session (operator prompts)
        and copied into later batch folders. Stand-ins without a spectrometer are left alone.
        """
        for view in ("ssy", "sy", "spec"):
            labware = getattr(self.experiment, f"samples{slot}_{view}", None)
            for well in wells if labware is not None else ():
                labware[well].currentLiquidVolume = 0
                labware[well].num_readings_taken = 0
                labware[well].next_spectrum_recordtime = None
        spec = getattr(self.experiment, "spectrometer", None)
        if not hasattr(spec, "experiment_dir"):
            return
        old_refs, now = Path(spec.refs_dir), datetime.now()
        operator = spec.experiment_metadata.get("operator_name") or self.operator_name
        spec.experiment_id = f"{name}_{operator}_{now:%Y%m%d_%H%M%S}"
        spec.experiment_dir = Path(spec.base_dir) / f"{now:%Y-%m-%d}" / operator / spec.experiment_id
        spec.spectra_dir, spec.refs_dir = spec.experiment_dir / "spectra", spec.experiment_dir / "references"
        for d in (spec.spectra_dir, spec.refs_dir):
            d.mkdir(parents=True, exist_ok=True)
        for ref in old_refs.glob("*.npy"):
            shutil.copy2(ref, spec.refs_dir / ref.name)
        spec._dark_path = spec.refs_dir / Path(spec._dark_path).name
        spec._white_path = spec.refs_dir / Path(spec._white_path).name
        spec.plate_id, spec.plate_dir, spec.well_recipes = spec.experiment_id, spec.spectra_dir, {}
        spec.experiment_metadata = {**spec.experiment_metadata, "experiment_id": spec.experiment_id,
                                    "experiment_name": name, "created_at": now.isoformat(),
                                    "notes": f"Reaction-aware BO batch {self.iteration} ({name})"}
        spec._save_experiment_metadata()
        self.experiment.log_path = str(spec.experiment_dir / "operations_log.txt")
        self.experiment.experiment_dir = str(spec.experiment_dir)

    def _pause(self, status: str, message: str) -> dict:
        self.status = status
        self.save_state()
        self.logger.warning(message)
        return {"status": status, "message": message, **self.summary()}

    def _complete(self, reason: str) -> dict:
        self.status = "completed"
        self.save_state()
        self.logger.info(f"Campaign complete: {reason}.")
        return {"status": "completed", "message": reason, **self.summary()}

    def summary(self) -> dict:
        best = int(np.argmax(self.y)) if len(self.y) else None
        return {"phase": self.phase, "phase_name": PHASES[self.phase].name, "batches": self.iteration,
                "n_vials": int(len(self.X)),
                "unused_vial_positions": {str(s): self.rack.free(s) for s in self.rack.slots},
                "best_k_n": None if best is None else float(self.y[best]),
                "best_composition_mL": None if best is None else self.X[best].tolist(),
                "state_file": str(self.state_file)}
