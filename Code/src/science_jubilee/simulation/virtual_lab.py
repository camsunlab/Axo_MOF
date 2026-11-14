"""Synthetic ground truth and a stand-in for Experiment that writes robot-format files.

The ground truth is an illustrative closed-form map from composition to
Gualtieri parameters. Its constants were chosen so that values fall in the range
of the recorded BO_4 fits and so that low Co gives no reaction and low 2-mIm
gives no 590-nm feature, as observed for B4 and B2. They were not fitted to the
campaign and say nothing about real ZIF-67 kinetics.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import json
from pathlib import Path
import re

import matplotlib
matplotlib.use("Agg")
import numpy as np

from science_jubilee.analysis.gualtieri import gualtieri_model
from science_jubilee.optimization.sampling import build_feasible_lattice

WAVELENGTHS = np.round(np.arange(194.0, 1000.0 + 1e-9, 0.6), 1)
SIMULATION_EPOCH = datetime(2000, 1, 1, 9, 0, 0)


def _onset(x, centre=0.4, width=0.08):
    return 1.0 / (1.0 + np.exp(-(np.asarray(x, float) - centre) / width))


@dataclass(frozen=True)
class GroundTruth:
    """Composition (Co, 2-mIm, TEA in mL) -> synthetic kinetic parameters."""
    a_min: float = 10.0           # nucleation time at the k_n optimum (min)
    a_curvature: float = 24.0     # increase of a per unit squared scaled distance (min)
    k_n_optimum: tuple = (3.0, 2.5)
    k_n_scale: float = 2.0        # mL
    b_offset: float = 6.0         # b = b_offset + b_slope * a (min)
    b_slope: float = 0.25
    k_g_offset: float = 0.05      # k_g = k_g_offset + k_g_slope * Co (1/min)
    k_g_slope: float = 0.03
    I_max_peak: float = 0.75      # 400-nm amplitude at the I_max optimum (a.u.)
    I_max_optimum: tuple = (4.0, 3.0)
    I_max_width: float = 3.0      # mL
    feature_ratio: float = 0.8    # 590-nm amplitude relative to the 400-nm amplitude
    replicate_noise: float = 0.003   # per-spectrum offset s.d. (a.u.)
    pixel_noise: float = 0.002       # per-pixel s.d. (a.u.)

    def parameters(self, x) -> dict:
        co, mim = float(x[0]), float(x[1])
        d2 = ((co - self.k_n_optimum[0]) ** 2 + (mim - self.k_n_optimum[1]) ** 2) / self.k_n_scale ** 2
        a = self.a_min + self.a_curvature * d2
        d2_imax = (co - self.I_max_optimum[0]) ** 2 + (mim - self.I_max_optimum[1]) ** 2
        I_max = self.I_max_peak * float(_onset(co)) * np.exp(-d2_imax / (2 * self.I_max_width ** 2))
        return {"k_g": self.k_g_offset + self.k_g_slope * co, "a": a,
                "b": self.b_offset + self.b_slope * a, "I_max": float(I_max),
                "feature_amplitude": float(self.feature_ratio * I_max * _onset(mim))}

    def true_k_n(self, x) -> float:
        return 1.0 / self.parameters(x)["a"]

    def optimum(self, objective: str, total_ml=8.0, step_ml=0.1) -> tuple[np.ndarray, float]:
        """Best lattice point for 'k_n' or 'I_max', ignoring optical gates."""
        lattice = build_feasible_lattice(total_ml=total_ml, step_ml=step_ml)
        values = np.array([self.true_k_n(x) if objective == "k_n" else self.parameters(x)["I_max"]
                           for x in lattice])
        best = int(np.argmax(values))
        return lattice[best], float(values[best])


def _band(centre, width):
    return np.exp(-0.5 * ((WAVELENGTHS - centre) / width) ** 2)


BASELINE = 0.02 + 0.03 * np.exp(-(WAVELENGTHS - WAVELENGTHS[0]) / 80.0)
BAND_400 = _band(400.0, 40.0)
BAND_590 = _band(590.0, 30.0)


def _header(meta: dict) -> str:
    lines = ["# ---", *(f"# {k}: {v}" for k, v in meta.items()), "# ---"]
    if len(lines) != 31:  # read_uvvis skips exactly 31 rows
        raise AssertionError(f"Spectrum header must have 31 lines, not {len(lines)}.")
    return "\n".join(lines) + "\n"


def write_batch(plan: dict, out_dir: Path, truth: GroundTruth, seed: int,
                start: datetime = SIMULATION_EPOCH, noise: bool = True) -> dict:
    """Write one simulated batch in the recorded Dataset/<batch>/ layout.

    Returns the true parameters for each vial. Spectra are recorded relative to
    each vial's baseline visit, as in Experiment.record_spectrum; every later visit
    drifts by the recording overhead, so elapsed times are irregular.
    """
    rng = np.random.default_rng(seed)
    out_dir = Path(out_dir)
    (out_dir / "spectra").mkdir(parents=True, exist_ok=True)
    interval = int(plan["spectrum_record_interval_mins"])
    n_records = int(plan["max_spectrum_records"])
    name = plan.get("Experiment", {}).get("experiment_name", out_dir.name)
    vials = [(int(key[4:]), vial, vol) for key in sorted(plan) if re.fullmatch(r"slot\d+", key)
             for vial, vol in plan[key].items()]
    metadata = {"experiment_id": out_dir.name, "experiment_name": name, "operator_name": "simulation",
                "target_compound": "MOF", "project_id": None, "sample_type": "MOF_synthesis",
                "solvent": "triethylamine/methanol", "temperature_c": 25,
                "notes": f"simulated batch of {len(vials)} vials; no hardware",
                "created_at": start.isoformat(), "instrument_serial": "simulated", "simulated": True}
    log, recipes, truths = [(start, "Simulated batch started; no hardware is connected")], {}, {}
    for i, (slot, vial, vol) in enumerate(vials):
        x = (vol["metal_precursor_vol"], vol["organic_precursor_vol"], vol["solvent_vol"])
        p = truth.parameters(x)
        truths[vial] = {"Co_mL": x[0], "mIm_mL": x[1], "TEA_mL": x[2], **p, "k_n": 1.0 / p["a"]}
        recipes[vial] = {"metal_precursor": {"name": "Co+2", "volume_ml": x[0]},
                         "organic_precursor": {"name": "2-methylimidazole", "volume_ml": x[1]},
                         "solvent": {"name": "triethylamine/methanol", "volume_ml": x[2]},
                         "total_volume_ml": float(sum(x)), "notes": f"slot{slot}"}
        visit = start + timedelta(seconds=90 * (i + 1))
        baseline_visit, columns = visit, {}
        for n in range(n_records):
            if n:
                drift = int(rng.integers(0, 61)) if noise else 0
                visit += timedelta(minutes=interval, seconds=drift)
            t = (visit - baseline_visit).total_seconds() / 60.0
            progress = float(gualtieri_model(np.array([t]), p["k_g"], p["a"], p["b"], 1.0)[0])
            clean = BASELINE + progress * (p["I_max"] * BAND_400 + p["feature_amplitude"] * BAND_590)
            for r, suffix in enumerate(("", "-2", "-3")):
                spectrum = clean.copy()
                if noise:
                    spectrum += rng.normal(0, truth.replicate_noise) + rng.normal(0, truth.pixel_noise, clean.size)
                columns[f"{n * interval} min{suffix}"] = spectrum
            log.append((visit, f"Spectrum recorded for vial {vial} on Slot {slot} at {n * interval} mins"))
        meta = {**{k: v for k, v in metadata.items() if k != "simulated"},
                "measurement_timestamp": baseline_visit.isoformat(), "well_id": vial,
                "slot": f"simulated vial {vial} on slot {slot}", "pixels": WAVELENGTHS.size,
                "integration_time_us": 0, "scans_averaged": 0, "boxcar_width": 0,
                "dark_id": "simulated", "white_id": "simulated", "simulated": "true",
                "absorbance_unit": "a.u.", "metal_precursor_name": "Co+2", "metal_precursor_vol_ml": x[0],
                "organic_precursor_name": "2-methylimidazole", "organic_precursor_vol_ml": x[1],
                "solvent_name": "triethylamine/methanol", "solvent_vol_ml": x[2],
                "total_volume_ml": float(sum(x))}
        table = np.column_stack([WAVELENGTHS, *columns.values()])
        with open(out_dir / "spectra" / f"{vial}.csv", "w", encoding="utf-8", newline="\n") as f:
            f.write(_header(meta))
            f.write(",".join(["wavelength_nm", *columns]) + "\n")
            np.savetxt(f, table, delimiter=",", fmt="%.10g")
    end = max(t for t, _ in log)
    log.append((end, "Experiment Completed Successfully!!"))
    log.sort(key=lambda item: item[0])
    (out_dir / "operations_log.txt").write_text(
        "".join(f"[{t:%Y-%m-%d %H:%M:%S}] {msg}\n" for t, msg in log), encoding="utf-8")
    (out_dir / "mof_recipes.json").write_text(json.dumps(recipes, indent=2) + "\n", encoding="utf-8")
    (out_dir / "experiment_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    (out_dir / "ground_truth.json").write_text(json.dumps(truths, indent=2) + "\n", encoding="utf-8")
    return truths


class SimulatedExperiment:
    """Duck-typed stand-in for science_jubilee.Experiment used by the BO orchestrator.

    The orchestrator needs only make_batch(plan_path) and experiment_dir. Upstream
    Experiment never sets experiment_dir (only the Spectrometer has one, fixed at
    construction), so each simulated batch gets its own directory here instead.
    """

    def __init__(self, base_dir, truth: GroundTruth | None = None, seed: int = 42, noise: bool = True):
        self.base_dir = Path(base_dir) / "batches"
        self.truth = truth or GroundTruth()
        self.seed = int(seed)
        self.noise = noise
        # Continue numbering after a resume instead of overwriting earlier batches.
        self.n_batches = len(list(self.base_dir.glob("batch_*"))) if self.base_dir.exists() else 0
        self.experiment_dir: str | None = None
        self.history: list[dict] = []

    def make_batch(self, file_name: str) -> None:
        plan = json.loads(Path(file_name).read_text(encoding="utf-8"))
        self.n_batches += 1
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", plan.get("Experiment", {}).get("experiment_name", "batch"))
        out_dir = self.base_dir / f"batch_{self.n_batches:02d}_{name}"
        start = SIMULATION_EPOCH + timedelta(days=self.n_batches - 1)
        truths = write_batch(plan, out_dir, self.truth, seed=self.seed * 1000 + self.n_batches,
                             start=start, noise=self.noise)
        self.experiment_dir = str(out_dir)
        self.history.append({"batch": self.n_batches, "dir": str(out_dir), "truth": truths})


def describe(truth: GroundTruth) -> dict:
    """Ground-truth constants and optima for the run summary."""
    k_n_x, k_n_best = truth.optimum("k_n")
    imax_x, imax_best = truth.optimum("I_max")
    return {"constants": asdict(truth), "k_n_optimum": {"composition_mL": k_n_x.tolist(), "k_n": k_n_best},
            "I_max_optimum": {"composition_mL": imax_x.tolist(), "I_max": imax_best},
            "statement": "Synthetic, illustrative ground truth; not fitted to the campaign and not a model of real ZIF-67 kinetics."}
