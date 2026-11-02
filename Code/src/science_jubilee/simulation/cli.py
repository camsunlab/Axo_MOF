"""Run the hardware-independent simulation mode; all measurements are synthetic."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import time

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from science_jubilee.simulation.virtual_lab import GroundTruth, describe

DIRECT_PACKAGES = ("axo-mof", "numpy", "scipy", "pandas", "scikit-learn", "matplotlib", "python-ternary")


def _plot_progress(output: Path, summary: dict) -> None:
    panels = [(name, summary[name]) for name in ("upstream", "policy") if name in summary]
    fig, axes = plt.subplots(1, len(panels), figsize=(5.2 * len(panels), 3.8), squeeze=False)
    for ax, (name, s) in zip(axes.flat, panels):
        if name == "upstream":
            values = [it["best_true_I_max_so_far"] / s["true_I_max_optimum"] for it in s["iterations"]]
            ax.set_title("Upstream loop (I_max objective)")
        else:
            values = [r["best_measured_true_k_n"] / s["true_k_n_optimum"] for r in s["rounds"]]
            ax.set_title("Two-mode policy (k_n objective)")
        ax.plot(range(len(values)), values, "o-", color="#3B6D9A")
        ax.axhline(1.0, color="#999999", lw=0.8, ls="--")
        ax.set(xlabel="Batch", ylabel="True value of best point / true optimum", ylim=(0, 1.05))
        ax.spines[["top", "right"]].set_visible(False)
    fig.suptitle("Simulated runs on a synthetic ground truth", fontsize=10)
    fig.tight_layout()
    fig.savefig(output / "progress.png", dpi=150)
    plt.close(fig)


def run_simulation(output="simulation_outputs", loop="both", seed=42, rounds=6, phases="multi",
                   batch_size=5, max_iterations=4, max_total_iterations=25, progress=True) -> dict:
    started = time.perf_counter()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    truth = GroundTruth()
    summary = {"simulated": True, "scope": "Software exercise on a synthetic ground truth; not evidence about "
               "ZIF-67 kinetics or optimization performance on the physical system, and not a campaign replay.",
               "seed": int(seed), "ground_truth": describe(truth)}
    if loop in ("upstream", "both"):
        from science_jubilee.simulation.run_upstream import run_upstream
        summary["upstream"] = run_upstream(output, truth, seed, phases, batch_size, max_iterations,
                                           max_total_iterations)
    if loop in ("policy", "both"):
        from science_jubilee.simulation.run_policy import run_policy
        summary["policy"] = run_policy(output, truth, seed, rounds, progress=progress)
    _plot_progress(output, summary)
    summary.update(python_version=platform.python_version(), platform=platform.platform(),
                   package_versions={n: importlib.metadata.version(n) for n in DIRECT_PACKAGES},
                   elapsed_seconds=time.perf_counter() - started)
    (output / "simulation_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if progress:
        print(f"Simulation finished in {summary['elapsed_seconds']:.1f} s. Output: {output}", flush=True)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("simulation_outputs"))
    parser.add_argument("--loop", choices=["upstream", "policy", "both"], default="both")
    parser.add_argument("--seed", type=int, default=42, help="Seed for simulated measurement noise.")
    parser.add_argument("--rounds", type=int, default=6, help="Rounds of the two-mode policy loop.")
    parser.add_argument("--phases", choices=["multi", "single"], default="multi",
                        help="Upstream phase configuration (multi = upstream DEFAULT_PHASES).")
    parser.add_argument("--batch-size", type=int, default=5, help="Upstream single-phase batch size.")
    parser.add_argument("--max-iterations", type=int, default=4, help="Upstream single-phase iterations.")
    parser.add_argument("--max-total-iterations", type=int, default=25,
                        help="Safety cap on upstream iterations; reaching it is reported.")
    parser.add_argument("--quick", action="store_true",
                        help="Short run: upstream single phase (3 vials, 2 iterations), 3 policy rounds.")
    args = parser.parse_args(argv)
    if args.quick:
        args.phases, args.batch_size, args.max_iterations, args.rounds = "single", 3, 2, 3
    run_simulation(args.output, args.loop, args.seed, args.rounds, args.phases, args.batch_size,
                   args.max_iterations, args.max_total_iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
