"""Recorded-data spectral analysis and BO recommendation; no hardware execution.

The existing Gualtieri and GP implementations are reused without changing their
mathematical definitions. Optical gates follow Code/gualtieri.ipynb. The BO
objective is explicitly k_n = 1/a, with a zero optimization score for optical
gate failures. This example is one analysis-and-recommendation cycle using a
single recorded batch, not a replay of the complete historical campaign.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path
import time
import warnings

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from science_jubilee.analysis.gualtieri import (
    read_uvvis, extract_series_at_wavelength, parse_times_from_operations_log,
    parse_uvvis_metadata, fit_gualtieri, gualtieri_model,
)
from science_jubilee.optimization.bayesian import (
    fit_gp, lattice_mu_sigma_ei, greedy_ei_with_radius,
)

DIRECT_PACKAGES = ("axo-mof", "numpy", "scipy", "pandas", "scikit-learn",
                   "matplotlib", "python-ternary")


def default_data_dir() -> Path:
    return Path(__file__).resolve().parent / "offline_example"


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_input_files(data_dir: Path) -> dict:
    """Verify the shipped data against the immutable-source manifest."""
    manifest_path = data_dir / "provenance.json"
    if not manifest_path.exists():
        return {"manifest_present": False, "files": []}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        path = (data_dir / entry["file"]).resolve()
        if not path.is_relative_to(data_dir.resolve()):
            raise ValueError("Input manifest contains a path outside the dataset.")
        if not path.is_file() or _sha256(path) != entry["sha256"]:
            raise ValueError(f"Input file failed its SHA-256 check: {entry['file']}")
    return {"manifest_present": True, **manifest}


def _analyse_vial(csv_path: Path, log_path: Path, recipe: dict) -> tuple[dict, dict, list[str]]:
    wavelengths, spectra = read_uvvis(str(csv_path))
    nominal, signal, wl400 = extract_series_at_wavelength(wavelengths, spectra, 400.0)
    _, feature, wl590 = extract_series_at_wavelength(wavelengths, spectra, 590.0)
    if len(nominal) < 4 or nominal[0] != 0:
        raise ValueError(f"{csv_path.name}: a baseline and at least four time points are required.")
    if abs(wl400 - 400.0) > 2 or abs(wl590 - 590.0) > 2:
        raise ValueError(f"{csv_path.name}: the expected 400/590-nm channels are unavailable.")
    vial = csv_path.stem
    meta = parse_uvvis_metadata(str(csv_path))
    if meta.get("well_id", vial).strip() != vial:
        raise ValueError(f"{csv_path.name}: well_id metadata does not match its filename.")
    # The archived recipe identifies the physical slot as 'slot2' or 'slot5'.
    notes = recipe.get("notes", "").strip().lower()
    slot = int(notes.removeprefix("slot")) if notes.startswith("slot") else None
    mapping = parse_times_from_operations_log(str(log_path), vial, slot)
    if not all(int(t) in mapping for t in nominal):
        raise ValueError(f"{vial}: every CSV time point must have a matching operation-log event.")
    elapsed = np.array([mapping[int(t)] for t in nominal], dtype=float)
    if elapsed[0] != 0 or np.any(np.diff(elapsed) <= 0):
        raise ValueError(f"{vial}: recorded times must increase from the baseline event.")
    volume = [float(recipe[key]["volume_ml"]) for key in
              ("metal_precursor", "organic_precursor", "solvent")]
    if not np.isclose(sum(volume), 8.0) or min(volume) < 0:
        raise ValueError(f"{vial}: the demonstration expects an 8-mL ternary formulation.")
    corrected = signal - np.min(signal)  # Matches the deposited fit_gualtieri.
    amplitude = float(np.ptp(signal))
    feature_amplitude = float(np.ptp(feature))
    status = "fitted"
    if float(np.max(signal)) < 0.12 or amplitude < 0.12:
        status = "no_reaction"
    elif feature_amplitude < 0.12:
        status = "no_target_feature"
    row = dict(vial_id=vial, Co_mL=volume[0], mIm_mL=volume[1], TEA_mL=volume[2],
               status=status, n_time_points=len(elapsed), wavelength_nm=wl400,
               feature_wavelength_nm=wl590, signal_range=amplitude,
               feature_range=feature_amplitude, first_followup_min=float(elapsed[1]),
               k_g_min_inverse=None, a_min=None, b_min=None, I_max=None, R2=None,
               k_n_min_inverse=None, optimization_score=0.0,
               alpha_first_followup=None, parameter_at_bound=False,
               a_outside_observed_window=False)
    trace = {"nominal_min": nominal, "time_min": elapsed, "raw_absorbance": signal,
             "corrected_signal": corrected, "feature_absorbance": feature,
             "alpha": np.full_like(elapsed, np.nan), "fit": None}
    captured = []
    if status == "fitted":
        with warnings.catch_warnings(record=True) as warning_records:
            warnings.simplefilter("always")
            fitted = fit_gualtieri(elapsed, signal)
        captured.extend(sorted({str(w.message) for w in warning_records}))
        parameters = np.asarray(fitted["params"], dtype=float)
        if not np.all(np.isfinite(parameters)) or not np.isfinite(fitted["r2"]):
            raise RuntimeError(f"{vial}: the nonlinear fit failed; no recommendation was produced.")
        kg, a, b, imax = parameters.tolist()
        alpha = corrected / imax
        at_bound = bool(np.any(np.isclose(parameters, [1e-5, .1, .1, .1], rtol=1e-4, atol=1e-8))
                        or np.any(np.isclose(parameters, [10, 1e5, 1e5, 10], rtol=1e-4, atol=1e-8)))
        row.update(k_g_min_inverse=kg, a_min=a, b_min=b, I_max=imax,
                   R2=float(fitted["r2"]), k_n_min_inverse=float(fitted["K_n"]),
                   optimization_score=float(fitted["K_n"]),
                   alpha_first_followup=float(alpha[1]), parameter_at_bound=at_bound,
                   a_outside_observed_window=bool(a > max(elapsed)))
        if a > max(elapsed):
            captured.append("Fitted a exceeds the observed time window; this is an extrapolated model parameter, not a directly resolved nucleation time.")
        trace.update(alpha=alpha, fit=fitted)
    return row, trace, captured


def _plot_outputs(output: Path, rows: list[dict], traces: dict,
                  lattice: np.ndarray, mean: np.ndarray, recommendations: pd.DataFrame) -> None:
    plt.rcParams.update({"font.size": 10, "svg.fonttype": "none"})
    ncols = 3
    nrows = int(np.ceil(len(rows) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(11.0, 3.3 * nrows), squeeze=False)
    for ax, row in zip(axes.flat, rows):
        tr = traces[row["vial_id"]]
        t = tr["time_min"]
        if row["status"] == "fitted":
            ax.scatter(t, tr["alpha"], facecolors="none", edgecolors="#3B6D9A", label="Observations")
            dense = np.linspace(0, max(t), 350)
            ax.plot(dense, gualtieri_model(dense, *tr["fit"]["params"]) / row["I_max"],
                    color="#BD604A", label="Gualtieri fit")
            ax.set_ylabel("Normalized optical signal")
            ax.set_title(f"{row['vial_id']}: a = {row['a_min']:.3g} min; R² = {row['R2']:.4f}")
            ax.legend(frameon=False, fontsize=8)
        else:
            ax.plot(t, tr["corrected_signal"], "o-", color="#777777", ms=4)
            ax.set_ylabel("Absorbance change (a.u.)")
            ax.set_title(f"{row['vial_id']}: {row['status'].replace('_', ' ')}")
        ax.set_xlabel("Time since baseline visit (min)")
        ax.set_xlim(left=0)
        ax.spines[["top", "right"]].set_visible(False)
    for ax in list(axes.flat)[len(rows):]:
        ax.set_visible(False)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(output / f"kinetic_fits.{ext}", dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.5, 5.5))
    color = ax.scatter(lattice[:, 0], lattice[:, 1], c=mean, s=11, cmap="viridis", rasterized=True)
    fig.colorbar(color, ax=ax, label="GP mean for optimization score (min⁻¹)")
    ax.scatter([r["Co_mL"] for r in rows], [r["mIm_mL"] for r in rows], s=65,
               facecolors="none", edgecolors="black", linewidths=1.1, label="Recorded formulations")
    ax.scatter(recommendations["Co_mL"], recommendations["mIm_mL"], marker="*", s=170,
               c="#D94C3D", edgecolors="white", linewidths=.7, label="Proposed next conditions")
    ax.set(xlabel="Co precursor solution (mL)", ylabel="2-mIm solution (mL)",
           xlim=(0, 8), ylim=(0, 8), title="Single-batch offline demonstration; TEA = 8 − Co − 2-mIm")
    ax.set_aspect("equal")
    ax.legend(frameon=False, loc="upper right", fontsize=8)
    fig.tight_layout()
    for ext in ("png", "svg"):
        fig.savefig(output / f"gp_recommendations.{ext}", dpi=180)
    plt.close(fig)


def analyse_batch(data: Path, progress=True) -> tuple[list[dict], dict, list[str]]:
    """Optical gates and Gualtieri fits for every vial of one recorded batch."""
    recipes = json.loads((data / "mof_recipes.json").read_text(encoding="utf-8"))
    paths = sorted((data / "spectra").glob("*.csv"))
    if not paths or set(p.stem for p in paths) != set(recipes):
        raise ValueError("Every recorded recipe must have exactly one spectral CSV.")
    rows, traces, notes = [], {}, []
    for path in paths:
        if progress:
            print(f"Processing {path.stem} ...", flush=True)
        row, trace, fit_warnings = _analyse_vial(path, data / "operations_log.txt", recipes[path.stem])
        rows.append(row)
        traces[path.stem] = trace
        notes.extend(f"{path.stem}: {w}" for w in fit_warnings)
    return rows, traces, notes


def mode_decision(rows: list[dict], threshold=0.5) -> dict:
    """Two-mode rule: any fitted first-follow-up alpha above threshold selects 2 vials at 5 min."""
    alphas = [r["alpha_first_followup"] for r in rows if r["status"] == "fitted"]
    maximum_alpha = max(alphas) if alphas else None
    switch = bool(alphas and maximum_alpha > threshold)
    next_batch_size, next_interval = (2, 5) if switch else (5, 15)
    return {"threshold": float(threshold), "max_alpha_first_followup": maximum_alpha,
            "switch_to_measurement_prioritized": switch,
            "next_mode": "measurement-prioritized" if switch else "throughput-prioritized",
            "next_batch_size": next_batch_size, "next_measurement_interval_min": next_interval}


def recommend(X: np.ndarray, y: np.ndarray, k: int, seed=42):
    """GP fit and greedy EI selection on the 8-mL / 0.1-mL lattice, excluding tested points."""
    bounds = [(0., 8.)] * 3
    with warnings.catch_warnings(record=True) as gp_warnings:
        warnings.simplefilter("always")
        gp, gp_info = fit_gp(X, y, bounds, n_restarts=20, random_state=int(seed))
    notes = sorted({"GP: " + str(w.message) for w in gp_warnings})
    lattice, unit_lattice, mu, sd, ei = lattice_mu_sigma_ei(gp, bounds, step_ml=0.1, xi=0.01)
    selected = greedy_ei_with_radius(lattice, unit_lattice, ei, X_seen=X,
                                     step_ml=0.1, k=k, radius_uc=0.2)
    if len(selected) != k:
        raise RuntimeError("Not enough positive-EI candidates to populate the proposed batch.")
    recommendations = pd.DataFrame(lattice[selected], columns=["Co_mL", "mIm_mL", "TEA_mL"])
    recommendations.insert(0, "proposal_rank", np.arange(1, len(selected) + 1))
    recommendations["predicted_score"] = mu[selected]
    recommendations["predictive_std"] = sd[selected]
    recommendations["expected_improvement"] = ei[selected]
    return recommendations, lattice, mu, gp_info, notes


def run_demo(data_dir=None, output_dir="offline_demo_outputs", seed=42,
             threshold=0.5, progress=True) -> dict:
    """Process one recorded batch and write a reproducible next-batch proposal."""
    started = time.perf_counter()
    data = Path(data_dir).resolve() if data_dir is not None else default_data_dir()
    output = Path(output_dir).resolve()
    if output == data or output.is_relative_to(data):
        raise ValueError("The output directory must be outside the read-only input dataset.")
    if not np.isfinite(threshold) or not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1.")
    if not 0 <= int(seed) < 2**32:
        raise ValueError("seed must be an integer from 0 to 2**32-1.")
    provenance = verify_input_files(data)
    rows, traces, notes = analyse_batch(data, progress)
    valid = [r for r in rows if r["status"] == "fitted"]
    if len(valid) < 2:
        raise ValueError("At least two fitted trajectories are required for this demonstration.")
    decision = mode_decision(rows, threshold)
    next_batch_size = decision["next_batch_size"]
    X = np.array([[r["Co_mL"], r["mIm_mL"], r["TEA_mL"]] for r in rows])
    y = np.array([r["optimization_score"] for r in rows])
    recommendations, lattice, mu, gp_info, gp_notes = recommend(X, y, next_batch_size, seed)
    notes.extend(gp_notes)
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(output / "kinetic_parameters.csv", index=False)
    observations = []
    for row in rows:
        tr = traces[row["vial_id"]]
        for j in range(len(tr["time_min"])):
            observations.append({"vial_id": row["vial_id"], "nominal_time_min": tr["nominal_min"][j],
                                 "elapsed_time_min": tr["time_min"][j],
                                 "absorbance_400": tr["raw_absorbance"][j],
                                 "absorbance_590": tr["feature_absorbance"][j],
                                 "baseline_corrected_signal": tr["corrected_signal"][j],
                                 "normalized_optical_signal": tr["alpha"][j]})
    pd.DataFrame(observations).to_csv(output / "processed_trajectories.csv", index=False)
    recommendations.to_csv(output / "next_conditions.csv", index=False)
    decision = {**decision, "executed": False, "proposals": recommendations.to_dict(orient="records")}
    _write_json(output / "next_batch_plan.json", decision)
    _plot_outputs(output, rows, traces, lattice, mu, recommendations)
    versions = {name: importlib.metadata.version(name) for name in DIRECT_PACKAGES}
    summary = {"example": "one recorded batch followed by an offline recommendation",
               "source_commit": provenance.get("commit"), "source_batch": provenance.get("source_batch", data.name),
               "n_recorded_vials": len(rows), "n_fitted": len(valid),
               "status_by_vial": {r["vial_id"]: r["status"] for r in rows},
               "seed": int(seed), "objective": "k_n=1/a; optical gate failures receive zero optimization score",
               "fit_baseline": "subtract the minimum observed 400-nm signal, as in the deposited fitter",
               "time_origin": "baseline Spectrum recorded operation-log event; not reagent addition",
               "alpha_normalization": "baseline-corrected 400-nm signal divided by fitted I_max, without clipping",
               "training_scope": "the supplied batch only; no historical-campaign or counterfactual replay",
               "input_hashes_verified": provenance["manifest_present"],
               "python_version": platform.python_version(), "platform": platform.platform(),
               "package_versions": versions, "gp": gp_info, "warnings": sorted(set(notes)),
               "decision": decision, "elapsed_seconds": time.perf_counter() - started}
    _write_json(output / "run_summary.json", summary)
    _write_json(output / "input_provenance.json", provenance)
    if progress:
        print(f"Finished: {len(valid)}/{len(rows)} fitted; {decision['next_mode']}; "
              f"{next_batch_size} proposed conditions. Output: {output}", flush=True)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None, help="Recorded batch folder; defaults to bundled data.")
    parser.add_argument("--output", type=Path, default=Path("offline_demo_outputs"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args(argv)
    run_demo(args.data_dir, args.output, args.seed, args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
