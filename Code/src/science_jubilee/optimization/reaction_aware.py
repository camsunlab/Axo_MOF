"""Reaction-aware batch Bayesian optimization, as described in the manuscript.

Phases (default, ``PHASES``)
----------------------------
0  initial sampling : the 5 maximin seed formulations of Code/sampling.ipynb, 15-min interval.
1  batch BO         : 5 vials per batch, 15-min measurement interval.
2  validation       : 2 vials per batch, 5-min measurement interval. Entered (one way) as soon as
                      any fitted vial of the latest batch has an extent of reaction alpha(t1) > 0.5
                      at its first follow-up reading.

Other measurement ladders are passed as ``phases=``; ``PHASES_60MIN`` is the 60-min protocol
(5 vials / 15 min / 60 min -> 2 vials / 5 min / 60 min -> 1 vial / 2 min / 24 min). Every phase
says how many readings a vial gets and which phase follows a batch with alpha(t1) above or below
the threshold.

Every batch is scored as in Code/gualtieri.ipynb: 400/590-nm optical gates, then a Gualtieri fit
at 400 nm with elapsed times from operations_log.txt; the objective is k_n = 1/a and gate failures
score 0. The 590-nm gate (Co(II) band of ZIF-67) can be switched off with feature_gate_nm=None (ZIF-8). The next batch is chosen as in Code/bayesian.ipynb: GP + EI on the 8-mL / 0.1-mL lattice,
xi = 0.01, exclusion radius 0.2, already-tested formulations excluded.

The loop saves its state and stops whenever the operator has to act: a phase change (optional),
no deck slot with enough unused vial positions, or (when reagent volumes are tracked) a reservoir
that would run too low during the next batch.
"""
from __future__ import annotations

import json
import re
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
    """One measurement mode of the loop.

    next_if_fast / next_if_slow: phase used after a batch whose largest alpha(t1) over the fitted vials
    is above / not above the threshold (None = stay in this phase).
    lids_between_readings: False keeps the sample/solvent lids off between readings (they still go on after
    the last reading of the round); used for short intervals where a ~100-s lid cycle would delay readings.
    """
    name: str
    batch_size: int
    interval_min: float
    n_records: int = 13
    seeded: bool = False
    next_if_fast: int | None = None
    next_if_slow: int | None = None
    lids_between_readings: bool = True

    @property
    def last_reading_min(self) -> float:
        return (self.n_records - 1) * self.interval_min


PHASES = {0: Phase("initial_sampling", 5, 15, 13, seeded=True, next_if_fast=2, next_if_slow=1),
          1: Phase("batch_bo", 5, 15, 13, next_if_fast=2, next_if_slow=1),
          2: Phase("validation", 2, 5, 13)}
# 60-min protocol: 5 vials, 15 min x 5 readings (0-60 min) -> 2 vials, 5 min x 13 (0-60 min)
# -> 1 vial, 2 min x 13 (0-24 min, lids off until the last reading).
PHASES_60MIN = {0: Phase("seed_5vials_15min", 5, 15, 5, seeded=True, next_if_fast=2, next_if_slow=1),
                1: Phase("bo_5vials_15min", 5, 15, 5, next_if_fast=2, next_if_slow=1),
                2: Phase("bo_2vials_5min", 2, 5, 13, next_if_fast=3, next_if_slow=2),
                3: Phase("bo_1vial_2min", 1, 2, 13, next_if_fast=3, next_if_slow=3, lids_between_readings=False)}
PHASE_PRESETS = {"manuscript": PHASES, "60min": PHASES_60MIN}
ALPHA_THRESHOLD = 0.5
XI = 0.01
RADIUS_UC = 0.2
N_RECORDS = 13
TOTAL_ML = 8.0
STEP_ML = 0.1
BOUNDS = [(0.0, TOTAL_ML)] * 3
COLUMNS = ("Co_mL", "mIm_mL", "TEA_mL")
REAGENTS = ("metal", "organic", "solvent")   # same order as COLUMNS
# Two 10-vial racks (wells A1-A5, B1-B5) on deck slots 2 and 5.
VIAL_SLOTS = (2, 5)
VIAL_WELLS = tuple(f"{row}{col}" for row in "AB" for col in range(1, 6))


class OperatorActionRequired(RuntimeError):
    """The loop cannot continue without the operator (state has been saved)."""


def _number(x: float):
    """15.0 -> 15, 2.5 -> 2.5 (plans and labels never show '5.0')."""
    x = round(float(x), 6)
    return int(x) if x.is_integer() else x


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

    A batch is always placed inside one slot (Experiment handles one rack per batch most reliably).
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


DEFAULT_REAGENT_NAMES = {"metal": "Co+2", "organic": "2-methylimidazole", "solvent": "triethylamine/methanol"}


def write_plan(path: Path, X: np.ndarray, slot: int, wells: list[str], interval_min: float,
               experiment_name: str, operator_name: str, description: str, n_records: int = N_RECORDS,
               reagent_names: dict | None = None) -> str:
    """Synthesis plan in the schema Experiment.make_batch reads (see Code/draft_synthesis_plan.json)."""
    names = {**DEFAULT_REAGENT_NAMES, **(reagent_names or {})}
    now = datetime.now()
    plan = {f"slot{slot}": {well: {"metal_precursor_vol": _number(x[0]), "organic_precursor_vol": _number(x[1]),
                                   "solvent_vol": _number(x[2])} for well, x in zip(wells, X)},
            "spectrum_record_interval_mins": _number(interval_min),
            "max_spectrum_records": int(n_records),
            "Experiment": {"operator_name": operator_name, "experiment_name": experiment_name,
                           "experiment_date": now.strftime("%d %B %Y"), "experiment_time": now.strftime("%H:%M"),
                           "experiment_description": description,
                           "approximate_time": f"{(n_records - 1) * interval_min / 60:.1f} hours of readings",
                           "metal_precursor_name": names["metal"], "organic_precursor_name": names["organic"],
                           "solvent_name": names["solvent"]}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=4) + "\n", encoding="utf-8")
    return str(path)


def analyse_batch_dir(batch_dir: Path, feature_nm: float | None = 590.0,
                      signal_gate: float = 0.12) -> tuple[list[dict], list[str]]:
    """Gates + Gualtieri fit for every vial; a vial whose fit fails scores 0 instead of halting the run."""
    recipes = json.loads((batch_dir / "mof_recipes.json").read_text(encoding="utf-8"))
    paths = sorted((batch_dir / "spectra").glob("*.csv"))
    if not paths or {p.stem for p in paths} != set(recipes):
        raise ValueError(f"{batch_dir}: every recorded recipe must have exactly one spectral CSV.")
    rows, notes = [], []
    for path in paths:
        try:
            row, _, warnings = _analyse_vial(path, batch_dir / "operations_log.txt", recipes[path.stem],
                                             feature_nm=feature_nm, signal_gate=signal_gate)
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

    Extra options (all off by default, so the manuscript behaviour is unchanged):
    phases             measurement ladder, e.g. PHASES_60MIN
    max_total_batches  stop after this many measured batches (rounds)
    initial_conditions (n, 3) mL compositions for the seeded phase instead of the maximin seeds
    reagents_ml        {"metal": mL, "organic": mL, "solvent": mL} now in the reservoirs; the loop
                       pauses with 'paused_refill_reagents' before a batch would leave less than
                       reagent_margin_ml in one of them (a syringe refill draws a full syringe, ~10.7 mL)
    keep_dual_syringe_between_batches
                       park the dual syringe with its precursor solutions between rounds; it is emptied
                       back into the reservoirs only after the last round (max_total_batches)
    feature_gate_nm    590.0 (ZIF-67) or None: no second optical gate, only the 400-nm signal (ZIF-8)
    signal_gate        absorbance threshold of the optical gates (0.12, as in gualtieri.ipynb)
    reagent_names      {"metal": ..., "organic": ..., "solvent": ...} written to the plans / recipes
    On resume (load_state) the dark/white references of the last recorded batch are reused, so the
    operator is not asked for them again.
    """

    def __init__(self, experiment, output_dir="optimization_results", operator_name="Operator",
                 n_initial_samples=5, alpha_threshold=ALPHA_THRESHOLD, max_bo_batches=10,
                 max_validation_batches=4, max_total_vials=None, pause_on_phase_change=True,
                 vial_slots=VIAL_SLOTS, seed=42, state_file="bo_state.json", save_plots=True,
                 phases=None, max_total_batches=None, initial_conditions=None,
                 reagents_ml=None, reagent_margin_ml=12.0, keep_dual_syringe_between_batches=False,
                 feature_gate_nm=590.0, signal_gate=0.12, reagent_names=None):
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
        self.max_total_batches = None if max_total_batches is None else int(max_total_batches)
        self.pause_on_phase_change = pause_on_phase_change
        self.seed = int(seed)
        self.save_plots = save_plots
        self.phases = dict(PHASES if phases is None else phases)
        self._check_phases()
        # The manuscript ladder keeps its two stopping rules; a custom ladder stops on max_total_batches.
        self.phase_batch_limits = ({1: (self.max_bo_batches, f"{self.max_bo_batches} batch-BO batches without "
                                        f"alpha(t1) > {self.alpha_threshold}"),
                                    2: (self.max_validation_batches,
                                        f"{self.max_validation_batches} validation batches completed")}
                                   if phases is None else {})
        self.initial_conditions = None if initial_conditions is None else self._check_conditions(initial_conditions)
        self.reagents = None if reagents_ml is None else {k: float(reagents_ml[k]) for k in REAGENTS}
        self.reagent_margin_ml = float(reagent_margin_ml)
        self.keep_dual_syringe_between_batches = bool(keep_dual_syringe_between_batches)
        self.feature_gate_nm = None if feature_gate_nm is None else float(feature_gate_nm)
        self.signal_gate = float(signal_gate)
        self.reagent_names = {**DEFAULT_REAGENT_NAMES, **(reagent_names or {})}
        self.logger = setup_bo_logger(str(self.output_dir))
        self.rack = VialRack(vial_slots)
        self.X, self.y = np.empty((0, 3)), np.empty(0)
        self.phase, self.iteration = 0, 0
        self.phase_batches = {p: 0 for p in self.phases}
        self.history: list[dict] = []
        self.pending: dict | None = None
        self.status = "new"
        self.session_batches = 0
        self._sync_labware_volumes()

    # ------------------------------------------------------------- validation
    def _check_phases(self) -> None:
        if sorted(self.phases) != list(range(len(self.phases))):
            raise ValueError("phases must be numbered 0, 1, 2, ...")
        for p, ph in self.phases.items():
            for nxt in (ph.next_if_fast, ph.next_if_slow):
                if nxt is not None and nxt not in self.phases:
                    raise ValueError(f"phase {p} points to unknown phase {nxt}")
            if ph.n_records < 4:
                raise ValueError(f"phase {p}: the Gualtieri fit needs at least 4 readings per vial")

    @staticmethod
    def _check_conditions(conditions) -> np.ndarray:
        X = np.round(np.asarray(conditions, float).reshape(-1, 3), 6)
        units = X / STEP_ML
        if not np.allclose(units, np.round(units), atol=1e-6) or np.any(X < 0) \
                or not np.allclose(X.sum(axis=1), TOTAL_ML):
            raise ValueError(f"initial_conditions must be non-negative multiples of {STEP_ML} mL summing to {TOTAL_ML} mL")
        return X

    def _phase_settings(self) -> dict:
        return {str(p): vars(ph) for p, ph in self.phases.items()}

    # ------------------------------------------------------------------ state
    def save_state(self) -> None:
        state = {"policy": "reaction-aware", "phase": self.phase, "phase_name": self.phases[self.phase].name,
                 "iteration": self.iteration, "phase_batches": {str(p): n for p, n in self.phase_batches.items()},
                 "X": self.X.tolist(), "y": self.y.tolist(), "vials_used": self.rack.state(),
                 "pending_batch": self.pending, "history": self.history, "status": self.status,
                 "reagents_remaining_ml": self.reagents,
                 "settings": {"alpha_threshold": self.alpha_threshold, "max_bo_batches": self.max_bo_batches,
                              "max_validation_batches": self.max_validation_batches,
                              "max_total_vials": self.max_total_vials, "max_total_batches": self.max_total_batches,
                              "seed": self.seed, "xi": XI, "radius_uc": RADIUS_UC,
                              "reagent_margin_ml": self.reagent_margin_ml,
                              "feature_gate_nm": self.feature_gate_nm, "signal_gate": self.signal_gate,
                              "reagent_names": self.reagent_names,
                              "initial_conditions": None if self.initial_conditions is None
                              else self.initial_conditions.tolist(),
                              "phases": self._phase_settings()},
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
        saved, current = state.get("settings", {}).get("phases", {}), self._phase_settings()
        if set(saved) != set(current) or any(current[p].get(k) != v for p, s in saved.items() for k, v in s.items()):
            raise ValueError(f"{self.state_file} was written with a different phase ladder; "
                             "pass the same phases= as the run you are resuming.")
        settings = state.get("settings", {})
        for key in ("feature_gate_nm", "signal_gate"):
            if key in settings and settings[key] != getattr(self, key):
                raise ValueError(f"{self.state_file} was scored with {key}={settings[key]}, not {getattr(self, key)}; "
                                 "use the same optical gates as the run you are resuming.")
        self.phase, self.iteration = int(state["phase"]), int(state["iteration"])
        self.phase_batches = {int(p): int(n) for p, n in state["phase_batches"].items()}
        self.X = np.array(state["X"], float).reshape(-1, 3)
        self.y = np.array(state["y"], float)
        self.rack = VialRack(self.rack.slots, used=state["vials_used"])
        self.pending, self.history, self.status = state["pending_batch"], state["history"], state["status"]
        if state.get("reagents_remaining_ml") is not None:
            self.reagents = {k: float(v) for k, v in state["reagents_remaining_ml"].items()}
        self._sync_labware_volumes()
        self._restore_references()
        self.logger.info(f"Resumed at iteration {self.iteration}, phase {self.phase} ({self.phases[self.phase].name}); "
                         f"{self.rack.n_free()} unused vial positions; reagents left (mL): {self.reagents}.")

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

    def refill_reagents(self, metal=None, organic=None, solvent=None) -> None:
        """Record the volumes (mL) now in the reservoirs after the operator refilled them."""
        if self.reagents is None:
            self.reagents = {k: float("inf") for k in REAGENTS}
        for key, value in zip(REAGENTS, (metal, organic, solvent)):
            if value is not None:
                self.reagents[key] = float(value)
        self._sync_labware_volumes()
        self.logger.info(f"Reagents refilled; now (mL): {self.reagents}.")
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
        return {"status": "batch_cap_reached", "message": f"Stopped after {done} batch(es) in this call.",
                **self.summary()}

    def _n_measured(self) -> int:
        return sum(1 for h in self.history if not h.get("discarded"))

    def _run_batch(self) -> dict | None:
        phase = self.phases[self.phase]
        if self.max_total_batches is not None and self._n_measured() >= self.max_total_batches:
            return self._complete(f"{self.max_total_batches} batches completed")
        if self.max_total_vials is not None and len(self.X) >= self.max_total_vials:
            return self._complete(f"vial budget of {self.max_total_vials} reached")
        if phase.seeded:
            k = len(self.initial_conditions) if self.initial_conditions is not None else self.n_initial_samples
        else:
            k = phase.batch_size
        if self.max_total_vials is not None:
            k = min(k, self.max_total_vials - len(self.X))
        placement = self.rack.allocate(k)
        if placement is None:
            free = {s: len(self.rack.free(s)) for s in self.rack.slots}
            return self._pause("paused_vials_full",
                               f"No slot has {k} unused vial positions (unused per slot: {free}). Replace the "
                               "used vials with empty ones and resume with --vials-replaced.")
        if phase.seeded:
            X_next = (self.initial_conditions[:k] if self.initial_conditions is not None
                      else campaign_initial_sampling(k, random_state=self.seed))
            ei_max = float("nan")
        else:
            X_next, ei_max = self._select(k)
            if len(X_next) == 0:
                return self._complete("no formulation with positive expected improvement is left")
            placement = (placement[0], placement[1][:len(X_next)])
        need = dict(zip(REAGENTS, X_next.sum(axis=0).round(6).tolist()))
        short = self._reagent_shortfall(need)
        if short:
            return self._pause("paused_refill_reagents",
                               "Refill before the next batch: " + "; ".join(short) + ". Then call "
                               "refill_reagents(metal=..., organic=..., solvent=...) with the volumes now in the "
                               "reservoirs and run again.")
        slot, wells = placement
        self.iteration += 1
        name = f"P{self.phase}-{phase.name}-{self.phase_batches[self.phase] + 1:02d}"
        plan = write_plan(self.output_dir / "plans" / f"batch_{self.iteration:02d}_{name}.json", X_next, slot,
                          wells, phase.interval_min, name, self.operator_name,
                          f"Reaction-aware BO, phase {self.phase} ({phase.name}), {len(X_next)} vials",
                          n_records=phase.n_records, reagent_names=self.reagent_names)
        self.rack.mark_used(slot, wells)
        if self.reagents is not None:   # counted when the batch starts (conservative if it is interrupted)
            self.reagents = {r: self.reagents[r] - need[r] for r in REAGENTS}
        self.pending = {"iteration": self.iteration, "phase": self.phase, "experiment_name": name,
                        "plan": plan, "slot": slot, "wells": wells, "X": X_next.tolist(),
                        "interval_min": phase.interval_min, "n_records": phase.n_records, "ei_max": ei_max,
                        "reagents_ml": need, "status": "running", "batch_dir": None,
                        "started_at": datetime.now().isoformat()}
        self.status = "running"
        self.save_state()
        self.logger.info(f"Batch {self.iteration} ({name}): {len(X_next)} vials in slot {slot} {wells}, "
                         f"{_number(phase.interval_min)}-min interval x {phase.n_records} readings "
                         f"(0-{_number(phase.last_reading_min)} min).")
        last_round = self.max_total_batches is not None and self._n_measured() + 1 >= self.max_total_batches
        self._prepare_hardware(name, slot, wells, phase, last_round)
        try:
            self.experiment.make_batch(plan)
        except Exception as exc:  # keep the data if every reading was already recorded
            batch_dir = getattr(self.experiment, "experiment_dir", None)
            if batch_dir is None or not self._batch_complete(Path(batch_dir), slot, wells, phase.n_records):
                self.pending.update(status="failed", batch_dir=None if batch_dir is None else str(batch_dir),
                                    error=repr(exc), failed_at=datetime.now().isoformat())
                return self._pause("paused_hardware_error",
                                   f"make_batch raised {exc!r} before all readings were recorded. Check the deck "
                                   "(tools, lids, vials), then resume with --discard-pending.")
            self.logger.warning(f"make_batch raised {exc!r} after all {phase.n_records} readings of every vial were "
                                "recorded; the batch is analysed anyway. Check the deck before the next batch.")
        self.session_batches += 1
        self.pending.update(status="measured", batch_dir=str(self.experiment.experiment_dir),
                            finished_at=datetime.now().isoformat())
        self.save_state()
        return self._finish_batch()

    def _reagent_shortfall(self, need: dict) -> list[str]:
        if self.reagents is None:
            return []
        return [f"{r}: batch needs {need[r]:.1f} mL + {self.reagent_margin_ml:.0f} mL margin, "
                f"{self.reagents[r]:.1f} mL left" for r in REAGENTS
                if self.reagents[r] - need[r] < self.reagent_margin_ml]

    @staticmethod
    def _batch_complete(batch_dir: Path, slot: int, wells, n_records: int = N_RECORDS) -> bool:
        """True if operations_log.txt has n_records distinct readings for every vial of the batch."""
        log = batch_dir / "operations_log.txt"
        if not log.exists():
            return False
        text = log.read_text(encoding="utf-8", errors="replace")
        for well in wells:
            pat = re.compile(rf"Spectrum recorded for vial {well} on Slot {int(slot)} at (\d+(?:\.\d+)?) mins")
            if len(set(pat.findall(text))) < n_records:
                return False
        return True

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
            # Default gates use the one-argument call (the repo's replay test wraps analyse_batch_dir that way)
            gates = {} if (self.feature_gate_nm == 590.0 and self.signal_gate == 0.12) else \
                {"feature_nm": self.feature_gate_nm, "signal_gate": self.signal_gate}
            rows, notes = analyse_batch_dir(batch_dir, **gates)
        except Exception as exc:
            return self._pause("paused_analysis_failed",
                               f"Could not analyse {batch_dir}: {exc}. Fix the data, then resume "
                               "(or resume with --discard-pending).")
        planned = {tuple(np.round(x, 6)) for x in pending["X"]}
        measured = {tuple(np.round([r[c] for c in COLUMNS], 6)) for r in rows}
        if planned != measured:
            return self._pause("paused_analysis_failed",
                               f"{batch_dir}: recorded recipes do not match the plan {pending['plan']}.")
        for r in rows:
            if r.get("parameter_at_bound"):
                msg = (f"{r['vial_id']}: a Gualtieri parameter sits at its fit bound, so k_n = "
                       f"{r['optimization_score']:.3g} min^-1 is not resolved at a {pending['interval_min']}-min interval")
                notes.append(msg)
                self.logger.warning(msg)
        X = np.array([[r[c] for c in COLUMNS] for r in rows], float)
        y = np.array([r["optimization_score"] for r in rows], float)
        self.X, self.y = np.vstack([self.X, X]), np.concatenate([self.y, y])
        decision = mode_decision(rows, self.alpha_threshold)
        old = self.phase
        fast = decision["switch_to_measurement_prioritized"]
        nxt = self.phases[old].next_if_fast if fast else self.phases[old].next_if_slow
        if nxt is not None:
            self.phase = nxt
        self.phase_batches[old] += 1
        best = int(np.argmax(self.y))
        nphase = self.phases[self.phase]
        self.history.append({
            "iteration": pending["iteration"], "phase": old, "phase_name": self.phases[old].name,
            "experiment_name": pending["experiment_name"], "batch_dir": str(batch_dir),
            "slot": pending["slot"], "wells": " ".join(pending["wells"]), "n_vials": len(rows),
            "interval_min": pending["interval_min"], "n_records": pending.get("n_records", N_RECORDS),
            "ei_max": pending["ei_max"],
            "max_alpha_t1": decision["max_alpha_first_followup"], "alpha_threshold": self.alpha_threshold,
            "first_followup_min": " ".join(f"{r['vial_id']}={r['first_followup_min']:.1f}" for r in rows
                                           if r.get("first_followup_min") is not None),
            "next_phase": self.phase, "next_phase_name": nphase.name,
            "next_batch_size": nphase.batch_size, "next_interval_min": nphase.interval_min,
            "k_n_batch": " ".join(f"{r['vial_id']}={r['optimization_score']:.4g}({r['status']})" for r in rows),
            "best_k_n_so_far": float(self.y[best]), "best_composition_mL": " / ".join(f"{v:.1f}" for v in self.X[best]),
            "reagents_left_ml": None if self.reagents is None else
            " ".join(f"{k}={v:.1f}" for k, v in self.reagents.items()),
            "warnings": " | ".join(notes)})
        vials = pd.DataFrame([{"iteration": pending["iteration"], "phase": old, "batch": pending["experiment_name"],
                               "slot": pending["slot"], **r} for r in rows])
        vials_csv = self.output_dir / "vials.csv"
        if vials_csv.exists():   # re-write rather than append, so rounds with different columns stay aligned
            vials = pd.concat([pd.read_csv(vials_csv), vials], ignore_index=True)
        vials.to_csv(vials_csv, index=False)
        self.pending = None
        alpha = decision["max_alpha_first_followup"]
        self.logger.info(f"Batch {pending['iteration']}: k_n {[round(v, 4) for v in y]}, max alpha(t1) "
                         f"{'n/a' if alpha is None else round(alpha, 3)}; best k_n so far {self.y[best]:.4f}.")
        if self.max_total_batches is not None and self._n_measured() >= self.max_total_batches:
            return self._complete(f"{self.max_total_batches} batches completed")
        for p, (limit, reason) in self.phase_batch_limits.items():
            if self.phase == p and self.phase_batches[p] >= limit:
                return self._complete(reason)
        if self.phase != old:
            message = (f"Phase {old} ({self.phases[old].name}) -> phase {self.phase} ({nphase.name}); "
                       f"max alpha(t1) = {'n/a' if alpha is None else f'{alpha:.3f}'} "
                       f"(threshold {self.alpha_threshold}). Next: {nphase.batch_size} vials per "
                       f"batch at {_number(nphase.interval_min)}-min intervals x {nphase.n_records} readings.")
            self.logger.info(message)
            if self.pause_on_phase_change:
                return self._pause("paused_phase_change", message + " Resume to continue.")
        self.status = "running"
        self.save_state()
        return None

    # ---------------------------------------------------------------- helpers
    def _sync_labware_volumes(self) -> None:
        """Keep the Well volumes that the syringe tools check in line with the tracked reservoir volumes."""
        if self.reagents is None:
            return
        precursors = getattr(self.experiment, "precursors", None)
        solvents = getattr(self.experiment, "solvents", None)
        try:
            if precursors is not None:
                precursors[1].currentLiquidVolume = max(self.reagents["metal"], 0.0)    # drive 0, well A2
                precursors[0].currentLiquidVolume = max(self.reagents["organic"], 0.0)  # drive 1, well A1
            if solvents is not None:
                solvents[0].currentLiquidVolume = max(self.reagents["solvent"], 0.0)
        except (TypeError, IndexError, KeyError, AttributeError):
            pass

    def _restore_references(self) -> None:
        """After a restart, reuse the dark/white references of the last recorded batch (no operator prompt)."""
        spec = getattr(self.experiment, "spectrometer", None)
        if not hasattr(spec, "refs_dir") or not hasattr(self.experiment, "need_spectrum_refs"):
            return
        dirs = [h.get("batch_dir") for h in self.history if h.get("batch_dir")]
        if self.pending and self.pending.get("batch_dir"):
            dirs.append(self.pending["batch_dir"])
        for d in reversed(dirs):
            refs = sorted((Path(d) / "references").glob("*.npy"))
            if any(r.name.startswith("dark_") for r in refs) and any(r.name.startswith("white_") for r in refs):
                Path(spec.refs_dir).mkdir(parents=True, exist_ok=True)
                for ref in refs:
                    shutil.copy2(ref, Path(spec.refs_dir) / ref.name)
                spec._load_references()
                if spec.dark is not None and spec.white is not None:
                    self.experiment.need_spectrum_refs = False
                    self.logger.info(f"Reusing dark/white references {spec.dark_id} / {spec.white_id} from {d}.")
                return

    def _prepare_hardware(self, name: str, slot: int, wells: list[str], phase: Phase | None = None,
                          last_round: bool = False) -> None:
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
        self._sync_labware_volumes()
        if phase is not None and hasattr(self.experiment, "lids_between_readings"):
            self.experiment.lids_between_readings = phase.lids_between_readings
        if hasattr(self.experiment, "keep_dual_syringe_filled"):
            self.experiment.keep_dual_syringe_filled = self.keep_dual_syringe_between_batches and not last_round
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
        return {"phase": self.phase, "phase_name": self.phases[self.phase].name, "batches": self.iteration,
                "batches_measured": self._n_measured(), "n_vials": int(len(self.X)),
                "unused_vial_positions": {str(s): self.rack.free(s) for s in self.rack.slots},
                "reagents_left_ml": self.reagents,
                "best_k_n": None if best is None else float(self.y[best]),
                "best_composition_mL": None if best is None else self.X[best].tolist(),
                "state_file": str(self.state_file)}
