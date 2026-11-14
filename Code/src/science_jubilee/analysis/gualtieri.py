"""
Spectral data processing and Gualtieri fitting for MOF synthesis yield extraction.

This module provides functions to:
- Read UV-Vis spectroscopy data from CSV files
- Extract time-series data at specific wavelengths
- Fit Gualtieri growth model to crystallization kinetics
- Batch process multiple vials to extract MOF yields
"""

import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit
from sklearn.metrics import r2_score


def read_uvvis(path: str, skiprows: int = 31) -> Tuple[np.ndarray, pd.DataFrame]:
    """
    Read a UV–Vis CSV exported in wide format (wavelength + many spectra).

    Parameters
    ----------
    path : str
        CSV file path.
    skiprows : int, default=31
        Number of metadata rows to skip before numeric table begins.

    Returns
    -------
    wavelengths : np.ndarray
        1D array of wavelength values (nm)
    traces_df : pandas.DataFrame
        DataFrame containing each spectrum as a separate column
    """
    df = pd.read_csv(path, skiprows=skiprows, encoding="cp1252")
    df = df.apply(pd.to_numeric, errors="coerce").dropna(how="any")
    df = df.sort_values(df.columns[0])

    wavelengths = df.iloc[:, 0].values
    traces_df = df.drop(columns=df.columns[0])

    return wavelengths, traces_df


def extract_series_at_wavelength(
    wavelengths: np.ndarray,
    traces_df: pd.DataFrame,
    wavelength_target: float = 400.0
) -> Tuple[np.ndarray, np.ndarray, float]:
    """
    Extract time series at target wavelength using closest-two replicate averaging.

    Parameters
    ----------
    wavelengths : np.ndarray
        Array of wavelength values (nm)
    traces_df : pd.DataFrame
        DataFrame with columns like "15 min", "15 min-2", "15 min-3"
    wavelength_target : float, default=400.0
        Target wavelength (nm)

    Returns
    -------
    times_nominal : np.ndarray
        Nominal times (min) from column headers
    ys : np.ndarray
        Absorbance values (averaged across closest two replicates)
    wl_used : float
        Actual wavelength used (nearest to target)
    """
    idx = int(np.argmin(np.abs(wavelengths - wavelength_target)))
    wl_used = float(wavelengths[idx])
    row = traces_df.iloc[idx]

    # Parse column names like "15 min", "15 min-2", "15 min-3"
    pat = re.compile(r"^(\d+)\s*min(?:-(\d+))?$")
    t2vals: Dict[int, List[float]] = {}

    for col, val in row.items():
        m = pat.match(str(col))
        if m:
            t = int(m.group(1))
            t2vals.setdefault(t, []).append(float(val))

    def avg_closest_two(vals: List[float]) -> float:
        """Average the two closest values to reduce outlier impact."""
        vals = [v for v in vals if np.isfinite(v)]
        if len(vals) == 0:
            return np.nan
        if len(vals) == 1:
            return vals[0]
        # Find pair with minimum difference
        a, b = min(combinations(vals, 2), key=lambda ab: abs(ab[0] - ab[1]))
        return 0.5 * (a + b)

    times_nominal = np.array(sorted(t2vals.keys()), float)
    ys = np.array([avg_closest_two(t2vals[t]) for t in times_nominal], float)

    # Filter out NaN values
    m = np.isfinite(ys)
    return times_nominal[m], ys[m], wl_used


def parse_times_from_operations_log(
    log_path: str,
    vial_id: str,
    slot: Optional[int] = None,
) -> Dict[int, float]:
    """
    Parse operations_log.txt and return mapping from nominal to real elapsed time.

    Parameters
    ----------
    log_path : str
        Path to operations_log.txt
    vial_id : str
        Vial identifier (e.g., "A1", "B2")
    slot : int, optional
        Slot number (2 or 5). If None, matches any slot.

    Returns
    -------
    dict
        Mapping: nominal_minutes (int) -> real_time_since_t0 (float, minutes)
    """
    text = Path(log_path).read_text(encoding="utf-8")

    pat = re.compile(
        r"""
        ^\[
        (?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})
        \]\s+Spectrum\ recorded\ for\ vial\s+
        (?P<vial>\w+)\s+on\ Slot\s+
        (?P<slot>\d+)\s+at\s+
        (?P<nom>\d+)\s+mins
        """,
        re.VERBOSE | re.MULTILINE,
    )

    entries: List[Tuple[int, datetime]] = []
    for m in pat.finditer(text):
        if m.group("vial") != vial_id:
            continue
        slot_str = m.group("slot")
        if slot is not None and int(slot_str) != slot:
            continue

        ts_str = m.group("ts")
        nominal = int(m.group("nom"))
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        entries.append((nominal, ts))

    if not entries:
        raise ValueError(
            f"No spectrum entries for vial={vial_id!r}, slot={slot!r} in {log_path!r}"
        )

    # Choose t0 from the nominal 0-min entries (earliest if multiple)
    t0_candidates = [ts for nom, ts in entries if nom == 0]
    if t0_candidates:
        t0 = min(t0_candidates)
    else:
        # Fallback: earliest timestamp if there is no explicit 0-min entry
        t0 = min(ts for _, ts in entries)

    # For each nominal minute, keep the earliest timestamp
    nominal2ts: Dict[int, datetime] = {}
    for nom, ts in entries:
        if nom not in nominal2ts or ts < nominal2ts[nom]:
            nominal2ts[nom] = ts

    # Convert to minutes relative to t0
    nominal2t_real: Dict[int, float] = {}
    for nom, ts in nominal2ts.items():
        dt_min = (ts - t0).total_seconds() / 60.0
        nominal2t_real[nom] = dt_min

    return nominal2t_real


def gualtieri_model(t: np.ndarray, k_g: float, a: float, b: float, I_max: float) -> np.ndarray:
    """
    Gualtieri kinetic model for nucleation and growth.

    Model: I(t) = [1 - exp(-(k_g*t)³)] * sigmoid((t-a)/b) * I_max

    Parameters
    ----------
    t : np.ndarray
        Time array (min)
    k_g : float
        Growth rate constant
    a : float
        Nucleation center parameter (min)
    b : float
        Nucleation width parameter (min)
    I_max : float
        Maximum intensity change (proxy for yield)

    Returns
    -------
    np.ndarray
        Predicted intensity values
    """
    t = np.asarray(t, float)
    growth = 1.0 - np.exp(-(k_g * t)**3)
    x = np.clip((t - a) / b, -100, 100)
    nucleation = 1.0 / (1.0 + np.exp(-x))
    return growth * nucleation * I_max


def fit_gualtieri(
    t: np.ndarray,
    y: np.ndarray,
    maxfev: int = 20000
) -> Dict:
    """
    Fit Gualtieri model to time-series data using grid search initialization.

    Parameters
    ----------
    t : np.ndarray
        Time array (min)
    y : np.ndarray
        Absorbance values
    maxfev : int, default=20000
        Maximum function evaluations for curve_fit

    Returns
    -------
    dict
        Dictionary containing:
        - 'params': (k_g, a, b, I_max)
        - 'y_fit': fitted y values
        - 'r2': R² score
        - 'K_n': nucleation rate constant (1/a)
        - 'I_max': maximum intensity change (YIELD METRIC)
    """
    t = np.asarray(t, float)
    y0 = np.asarray(y, float) - np.nanmin(y)  # Baseline shift
    I0 = float(max(np.nanmax(y0), 1e-9))

    # Grid search for initialization
    kg_grid = np.linspace(0.001, 1, 5)
    a_grid = np.linspace(1, 100, 5)
    b_grid = np.linspace(1, 100, 5)
    bounds = ([1e-5, 0.1, 0.1, 0.1], [10, 1e5, 1e5, 10])

    best = {"r2": -np.inf}

    for kg0 in kg_grid:
        for a0 in a_grid:
            for b0 in b_grid:
                p0 = [kg0, a0, b0, I0]
                try:
                    popt, _ = curve_fit(
                        gualtieri_model, t, y0,
                        p0=p0, bounds=bounds, maxfev=maxfev
                    )
                    yfit = gualtieri_model(t, *popt)
                    r2 = r2_score(y0, yfit)
                    k_g, a, b, I_max = map(float, popt)

                    if r2 > best["r2"] and k_g > 0 and a > 0 and b > 0 and I_max > 0:
                        best = {
                            "params": (k_g, a, b, I_max),
                            "y_fit": yfit.astype(float),
                            "r2": float(r2),
                            "K_n": 1.0 / a,
                            "I_max": I_max
                        }
                except Exception:
                    pass

    # If no valid fit found, return placeholder
    if best["r2"] == -np.inf:
        best = {
            "params": (np.nan,) * 4,
            "y_fit": np.zeros_like(t),
            "r2": -np.inf,
            "K_n": np.nan,
            "I_max": I0
        }

    return best


def parse_uvvis_metadata(path: str) -> Dict[str, str]:
    """
    Parse '# key: value' metadata block at the top of the CSV.

    Parameters
    ----------
    path : str
        Path to CSV file with metadata header

    Returns
    -------
    dict
        Metadata key-value pairs
    """
    meta: Dict[str, str] = {}
    with open(path, "r", encoding="latin1") as f:
        for line in f:
            if not line.startswith("#"):
                break
            line = line[1:].strip()
            if not line or line.startswith("---"):
                continue
            if ":" in line:
                key, val = line.split(":", 1)
                meta[key.strip()] = val.strip()
    return meta


def gualtieri_from_csv(
    path: str,
    wavelength_nm: float = 400.0,
    operations_log_path: Optional[str] = None,
    slot: Optional[int] = None,
    subtract_t0: bool = True,
    remove_times: Optional[List[float]] = None,
) -> Tuple[Dict, float]:
    """
    Full pipeline: Read CSV → Extract time series → Fit Gualtieri model.

    Parameters
    ----------
    path : str
        Path to vial CSV file
    wavelength_nm : float, default=400.0
        Wavelength for analysis (nm)
    operations_log_path : str, optional
        Path to operations_log.txt for real timestamps
    slot : int, optional
        Slot number (2 or 5)
    subtract_t0 : bool, default=True
        If True, uses ΔI(t) = I(t) - I(t₀)
    remove_times : list of float, optional
        Times to exclude from fitting

    Returns
    -------
    fit : dict
        Gualtieri fit results (includes 'I_max' as yield metric)
    wl_used : float
        Actual wavelength used
    """
    if remove_times is None:
        remove_times = []

    # Read CSV
    wavelengths, traces_df = read_uvvis(path)

    # Extract time series
    times_nominal, ys, wl_used = extract_series_at_wavelength(
        wavelengths, traces_df, wavelength_nm
    )

    # Replace nominal times with real elapsed times if operations_log provided
    if operations_log_path is not None:
        meta = parse_uvvis_metadata(path)
        vial_id = meta.get("well_id", "").strip()
        if not vial_id:
            warnings.warn(f"No 'well_id' in metadata of {path}; using nominal times.")
        else:
            try:
                nominal2real = parse_times_from_operations_log(
                    operations_log_path, vial_id, slot
                )
                times = np.array([nominal2real.get(int(t), t) for t in times_nominal], float)
            except Exception as e:
                warnings.warn(f"Failed to parse operations_log: {e}. Using nominal times.")
                times = times_nominal
    else:
        times = times_nominal

    # Remove specified times
    if remove_times:
        mask = np.isin(times_nominal, remove_times, invert=True)
        times = times[mask]
        ys = ys[mask]

    # Subtract t0 if requested
    if subtract_t0 and len(ys) > 0:
        ys = ys - ys[0]

    # Fit Gualtieri model
    if len(times) < 4:
        warnings.warn(f"Insufficient data points ({len(times)}) for fitting. Setting I_max=0.0")
        fit = {
            "params": (np.nan,) * 4,
            "y_fit": np.zeros_like(times),
            "r2": -np.inf,
            "K_n": np.nan,
            "I_max": 0.0
        }
    else:
        fit = fit_gualtieri(times, ys)

    return fit, wl_used


def batch_extract_yields(
    experiment_dir: str,
    wavelength_nm: float = 400.0,
    slot_vial_map: Optional[Dict[int, List[str]]] = None
) -> Dict[str, float]:
    """
    Extract yields from all vials in a completed experiment.

    Parameters
    ----------
    experiment_dir : str
        Path to experiment directory (e.g., Dataset/BO-6_Zuyang_20251121_162515/)
    wavelength_nm : float, default=400.0
        Wavelength for Gualtieri fitting
    slot_vial_map : dict, optional
        Mapping of slot number to vial IDs, e.g., {2: ["A1", "A2"], 5: ["B1"]}
        If None, auto-detects all CSV files in spectra/ directory

    Returns
    -------
    dict
        Mapping: vial_id -> I_max (yield)
        Failed fits return 0.0 as per error handling policy
    """
    exp_path = Path(experiment_dir)
    spectra_dir = exp_path / "spectra"
    ops_log_path = exp_path / "operations_log.txt"

    if not spectra_dir.exists():
        raise FileNotFoundError(f"Spectra directory not found: {spectra_dir}")

    # Find all CSV files
    csv_files = list(spectra_dir.glob("*.csv"))

    if not csv_files:
        warnings.warn(f"No CSV files found in {spectra_dir}")
        return {}

    yields: Dict[str, float] = {}

    for csv_path in csv_files:
        vial_id = csv_path.stem  # e.g., "A1", "B2"

        # Determine slot from vial ID if slot_vial_map not provided
        if slot_vial_map is not None:
            slot = None
            for s, vials in slot_vial_map.items():
                if vial_id in vials:
                    slot = s
                    break
        else:
            # Default: A-vials in slot2, B-vials in slot5
            slot = 2 if vial_id.startswith("A") else 5 if vial_id.startswith("B") else None

        try:
            fit, wl_used = gualtieri_from_csv(
                str(csv_path),
                wavelength_nm=wavelength_nm,
                operations_log_path=str(ops_log_path) if ops_log_path.exists() else None,
                slot=slot,
                subtract_t0=True
            )

            I_max = fit.get("I_max", 0.0)

            # Handle failed fits (R² < 0 or NaN)
            if np.isnan(I_max) or fit.get("r2", -np.inf) < 0:
                warnings.warn(f"Gualtieri fit failed for {vial_id}. Setting yield=0.0")
                I_max = 0.0

            yields[vial_id] = float(I_max)

        except Exception as e:
            warnings.warn(f"Error processing {vial_id}: {e}. Setting yield=0.0")
            yields[vial_id] = 0.0

    return yields
