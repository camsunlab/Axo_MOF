"""
Phase management for multi-phase Bayesian optimization.

Handles phase transitions, criteria evaluation, bound refinement,
and phase summary generation.
"""

from typing import Dict, List, Tuple, Optional

import numpy as np

from .phase_config import PhaseConfig


class PhaseManager:
    """
    Manages optimization phases and transitions.

    Attributes
    ----------
    phases : list of PhaseConfig
        Ordered list of phases to execute
    approval_mode : str
        "manual" or "automatic" - controls transition approval
    current_phase_index : int
        Index of current phase in phases list
    phase_iteration : int
        Iteration count within current phase
    phase_start_samples : list of int
        Sample count at start of each phase
    """

    def __init__(self, phases: List[PhaseConfig], approval_mode: str = "manual"):
        """
        Initialize phase manager.

        Parameters
        ----------
        phases : list of PhaseConfig
            Ordered list of phases to execute
        approval_mode : str, default="manual"
            "manual" or "automatic" - applies to ALL phase transitions
        """
        if not phases:
            raise ValueError("Must provide at least one phase")

        if approval_mode not in ["manual", "automatic"]:
            raise ValueError(f"approval_mode must be 'manual' or 'automatic', got '{approval_mode}'")

        self.phases = phases
        self.approval_mode = approval_mode
        self.current_phase_index = 0
        self.phase_iteration = 0
        self.phase_start_samples = [0]  # First phase starts at sample 0

    @property
    def current_phase(self) -> PhaseConfig:
        """Get current phase configuration."""
        return self.phases[self.current_phase_index]

    @property
    def has_next_phase(self) -> bool:
        """Check if there are more phases after current."""
        return self.current_phase_index < len(self.phases) - 1

    @property
    def is_multi_phase(self) -> bool:
        """Check if running in multi-phase mode."""
        return len(self.phases) > 1

    def get_next_phase(self) -> Optional[PhaseConfig]:
        """
        Get next phase configuration.

        Returns
        -------
        PhaseConfig or None
            Next phase, or None if on last phase
        """
        if self.has_next_phase:
            return self.phases[self.current_phase_index + 1]
        return None

    def check_transition_criteria(
        self,
        iteration: int,
        best_yield: float,
        ei_max: float
    ) -> Tuple[bool, List[str]]:
        """
        Check if current phase should transition to next.

        Parameters
        ----------
        iteration : int
            Current iteration within phase
        best_yield : float
            Best yield observed so far (across all phases)
        ei_max : float
            Maximum Expected Improvement value

        Returns
        -------
        should_transition : bool
            True if transition criteria met
        reasons : list of str
            Reasons why transition is triggered (or why not)
        """
        if not self.has_next_phase:
            return False, ["No more phases available"]

        phase = self.current_phase
        next_phase = self.get_next_phase()
        reasons = []

        # Check minimum iterations
        if iteration + 1 < phase.min_iterations:
            return False, [f"Minimum iterations not reached ({iteration}/{phase.min_iterations})"]

        # Check maximum iterations (hard limit)
        if iteration >= phase.max_iterations:
            reasons.append(f"Maximum iterations reached ({iteration}/{phase.max_iterations})")
            return True, reasons

        # Check performance threshold for next phase
        if next_phase.performance_threshold is not None:
            if best_yield >= next_phase.performance_threshold:
                reasons.append(
                    f"Performance threshold met "
                    f"(yield={best_yield:.4f} >= {next_phase.performance_threshold})"
                )
            else:
                return False, [
                    f"Performance threshold not met "
                    f"(yield={best_yield:.4f} < {next_phase.performance_threshold})"
                ]

        # Check convergence
        if ei_max < phase.convergence_ei_threshold:
            reasons.append(
                f"EI converged "
                f"(EI={ei_max:.6f} < {phase.convergence_ei_threshold})"
            )

        # Transition if we have at least one reason
        if reasons:
            return True, reasons
        else:
            return False, ["Transition criteria not yet met"]

    def compute_refinement_bounds(
        self,
        X: np.ndarray,
        y: np.ndarray,
        top_percentile: float = 0.2,
        margin: float = 0.5
    ) -> List[Tuple[float, float]]:
        """
        Compute restricted bounds around top-performing region.

        Parameters
        ----------
        X : np.ndarray, shape (n, 3)
            All compositions tested
        y : np.ndarray, shape (n,)
            All yields observed
        top_percentile : float, default=0.2
            Consider top X% of samples (0.2 = top 20%)
        margin : float, default=0.5
            Margin (mL) to add around region bounds

        Returns
        -------
        bounds : list of (low, high) tuples
            Restricted bounds for [Co, MIM, TEA]
        """
        if len(y) == 0:
            # Fallback to full space if no data
            return [(0.0, 8.0), (0.0, 8.0), (0.0, 8.0)]

        # Find top samples
        threshold = np.percentile(y, 100 * (1 - top_percentile))
        top_indices = y >= threshold
        top_X = X[top_indices]

        if len(top_X) == 0:
            # Fallback if no samples meet threshold
            return [(0.0, 8.0), (0.0, 8.0), (0.0, 8.0)]

        # Compute bounds with margin
        bounds = []
        for dim in range(X.shape[1]):
            low = max(0.0, float(top_X[:, dim].min() - margin))
            high = min(8.0, float(top_X[:, dim].max() + margin))

            # Ensure bounds are valid
            if high <= low:
                low = max(0.0, low - 0.5)
                high = min(8.0, high + 0.5)

            bounds.append((low, high))

        return bounds

    def transition_to_next_phase(self, total_samples: int):
        """
        Advance to next phase.

        Parameters
        ----------
        total_samples : int
            Total number of samples collected so far
        """
        if not self.has_next_phase:
            raise RuntimeError("Cannot transition: already on last phase")

        self.current_phase_index += 1
        self.phase_iteration = 0
        self.phase_start_samples.append(total_samples)

    def generate_phase_summary(
        self,
        X: np.ndarray,
        y: np.ndarray,
        iteration: int,
        plots_dir: str
    ) -> Dict:
        """
        Generate summary statistics for current phase.

        Parameters
        ----------
        X : np.ndarray, shape (n, 3)
            All compositions tested
        y : np.ndarray, shape (n,)
            All yields observed
        iteration : int
            Current iteration within phase
        plots_dir : str
            Path to plots directory

        Returns
        -------
        dict
            Summary statistics
        """
        # Get samples from current phase
        phase_start = self.phase_start_samples[-1]
        phase_X = X[phase_start:]
        phase_y = y[phase_start:]

        if len(phase_y) == 0:
            # No samples in current phase yet
            return {
                "phase_name": self.current_phase.name,
                "iterations": iteration,
                "samples_in_phase": 0,
                "total_samples": len(y),
                "best_yield_in_phase": 0.0,
                "best_composition_in_phase": [0.0, 0.0, 0.0],
                "best_yield_overall": float(np.max(y)) if len(y) > 0 else 0.0,
                "best_composition_overall": X[int(np.argmax(y))].tolist() if len(y) > 0 else [0.0, 0.0, 0.0],
                "plots_dir": plots_dir
            }

        best_idx_in_phase = int(np.argmax(phase_y))
        best_idx_overall = int(np.argmax(y))

        return {
            "phase_name": self.current_phase.name,
            "iterations": iteration,
            "samples_in_phase": len(phase_y),
            "total_samples": len(y),
            "best_yield_in_phase": float(phase_y[best_idx_in_phase]),
            "best_composition_in_phase": phase_X[best_idx_in_phase].tolist(),
            "best_yield_overall": float(y[best_idx_overall]),
            "best_composition_overall": X[best_idx_overall].tolist(),
            "plots_dir": plots_dir
        }

    def get_state(self) -> Dict:
        """
        Get current state for persistence.

        Returns
        -------
        dict
            State dictionary
        """
        return {
            "current_phase_index": self.current_phase_index,
            "phase_iteration": self.phase_iteration,
            "phase_start_samples": self.phase_start_samples,
            "approval_mode": self.approval_mode
        }

    def set_state(self, state: Dict):
        """
        Restore state from dictionary.

        Parameters
        ----------
        state : dict
            State dictionary from get_state()
        """
        self.current_phase_index = state["current_phase_index"]
        self.phase_iteration = state["phase_iteration"]
        self.phase_start_samples = state["phase_start_samples"]
        self.approval_mode = state["approval_mode"]
