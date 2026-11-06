import json
import logging
import os
import datetime as dt
import csv, pathlib
import re 

from typing import Dict

from typing import Tuple, Union
import sys
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

sdk_path = r"C:\Program Files\Ocean Optics\OceanDirect SDK\Python"
sys.path.insert(0, sdk_path)
# this is the Ocean Optics SDK, which is (very unfortunately) not open-source
try:
    from oceandirect.OceanDirectAPI import OceanDirectAPI, OceanDirectError
except ImportError:
    raise ImportError(
        "The Ocean Optics SDK is not installed. Please install it from the Ocean Insight website."
    )

from science_jubilee.labware.Labware import Labware, Location, Well
from science_jubilee.tools.Tool import Tool, requires_active_tool


def _yaml_header(meta: Dict[str, object]) -> str:
    """Return a YAML‑style header string (every line starts with `# `) wrapped in
    `# ---` barriers so it's visually distinct and YAML parsers can pick it
    up with a trivial pre‑process that strips the leading "# ".
    """
    lines = ["# ---"]
    for k, v in meta.items():
        lines.append(f"# {k}: {v}")
    lines.append("# ---")
    return "\n".join(lines) + "\n"


def _parse_header(path: pathlib.Path) -> Tuple[Dict[str, str], int]:
    """Return (header_dict, header_line_count)."""
    header: Dict[str, str] = {}
    line_count = 0
    with path.open() as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            line_count += 1
            if line.strip() in ("# ---", "#---"):
                continue
            if ":" in line:
                key, val = line[2:].split(":", 1)  # drop "# " prefix
                header[key.strip()] = val.strip()
    return header, line_count


class Spectrometer(Tool, OceanDirectAPI):
    
    DEFAULT_DIR: pathlib.Path = pathlib.Path("Spectra")
    
    def __init__(self, 
                 index, 
                 name, 
                 base_dir: str | pathlib.Path,
                 # Enhanced experiment identification - REQUIRED for MOF synthesis
                 experiment_name: str,
                 operator_name: str,
                 target_compound: str | None = None,
                 project_id: str | None = None,
                 experiment_notes: str | None = None,
                 sample_type: str = "MOF_synthesis",
                 solvent: str = "methanol",
                 temperature_c: float | None = None,
                 plate_id: str | None = None,
                 ref_dark: str = "dark.npy",
                 ref_white: str = "white.npy"):
        
        super().__init__(index, name)
        
        # ---------------------General Spectro Setup ------------------------#
        self.name = name
        self.index = index
        self.ocean = OceanDirectAPI()
        self.spectrometer, self.device_id = self.open_spectrometer()
        
        # Generate unique experiment ID with timestamp
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.experiment_id = f"{experiment_name}_{operator_name}_{timestamp}"
        
        # ---------------------Enhanced Storage Hierarchy --------------------#
        # Creates: base_dir/YYYY-MM-DD/operator_name/experiment_id/
        self.base_dir = pathlib.Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # Organized by date/operator/experiment
        date_str = dt.datetime.now().strftime("%Y-%m-%d")
        self.experiment_dir = self.base_dir / date_str / operator_name / self.experiment_id
        self.experiment_dir.mkdir(parents=True, exist_ok=True)
        
        # Subdirectories for organization
        self.spectra_dir = self.experiment_dir / "spectra"
        self.refs_dir = self.experiment_dir / "references"
        for d in [self.spectra_dir, self.refs_dir]:
            d.mkdir(exist_ok=True)
        
        # Store comprehensive experiment metadata
        self.experiment_metadata = {
            "experiment_id": self.experiment_id,
            "experiment_name": experiment_name,
            "operator_name": operator_name,
            "target_compound": target_compound,
            "project_id": project_id,
            "sample_type": sample_type,
            "solvent": solvent,
            "temperature_c": temperature_c,
            "notes": experiment_notes,
            "created_at": dt.datetime.now().isoformat(),
            "instrument_serial": self.device_id,
        }
        
        # Initialize MOF recipe storage
        self.well_recipes = {}
        
        # Save experiment metadata immediately
        self._save_experiment_metadata()
        
        # Update paths to use new structure
        self.plate_id = plate_id or self.experiment_id
        self.plate_dir = self.spectra_dir  # CSV files go in spectra/ subdirectory
        
        # -----------------Reference Filenames (White&Dark)-------------------------#
        self._dark_path = self.refs_dir / ref_dark
        self._white_path = self.refs_dir / ref_white
        
        # Cached Dark/White Spectra
        self.dark : np.ndarray | None = None
        self.white : np.ndarray | None = None
        self.dark_id: str | None = None
        self.white_id: str | None = None
        
        # Placeholder for last move
        self.current_well      = None
        self.current_location  = None
        
        # Try loading previous refs
        self._load_references()
        
        logging.info("Opened Spectrometer %s", self.device_id)
    
    # ------------------------- METHODS FOR ENHANCED LOGGING ----------------------------
    
    def _save_experiment_metadata(self):
        """Save comprehensive experiment metadata to JSON file."""
        metadata_file = self.experiment_dir / "experiment_metadata.json"
        with metadata_file.open("w") as f:
            json.dump(self.experiment_metadata, f, indent=2)
    
    def record_mof_recipe(self, 
                         well_id: str,
                         metal_precursor_name: str,
                         metal_precursor_vol_ml: float,
                         organic_precursor_name: str, 
                         organic_precursor_vol_ml: float,
                         solvent_name: str = "methanol",
                         solvent_vol_ml: float = None,
                         additional_notes: str = None):
        """Store MOF synthesis recipe for a specific well."""
        
        recipe = {
            "metal_precursor": {
                "name": metal_precursor_name,
                "volume_ml": metal_precursor_vol_ml
            },
            "organic_precursor": {
                "name": organic_precursor_name, 
                "volume_ml": organic_precursor_vol_ml
            },
            "solvent": {
                "name": solvent_name,
                "volume_ml": solvent_vol_ml
            },
            "total_volume_ml": metal_precursor_vol_ml + organic_precursor_vol_ml + (solvent_vol_ml or 0),
            # "molar_ratio": f"{metal_precursor_vol_ml}:{organic_precursor_vol_ml}",
            # "preparation_time": dt.datetime.now().isoformat(),
            "notes": additional_notes
        }
        
        # Store recipe for this well
        self.well_recipes[well_id] = recipe
        
        # Save all recipes to JSON file
        recipe_file = self.experiment_dir / "mof_recipes.json"
        with recipe_file.open("w") as f:
            json.dump(self.well_recipes, f, indent=2)
    
    def _get_enhanced_metadata(self, well_id: str, additional_meta: Dict = None) -> Dict:
        """Generate comprehensive metadata for each measurement."""
        base_meta = {
            # Experiment information
            **self.experiment_metadata,
            
            # Measurement-specific information
            "measurement_timestamp": dt.datetime.now().isoformat(),
            "well_id": well_id,
            "slot": str(self.current_location) if self.current_location else "unknown",
            
            # Instrument settings
            "pixels": len(self.spectrometer.get_wavelengths()) if hasattr(self, 'spectrometer') else 0,
            "integration_time_us": self.spectrometer.get_integration_time() if hasattr(self.spectrometer, 'get_integration_time') else 0,
            "scans_averaged": getattr(self.spectrometer, 'get_scans_to_average', lambda: 0)(),
            "boxcar_width": getattr(self.spectrometer, 'get_boxcar_width', lambda: 0)(),
            
            # Reference spectra information
            "dark_id": self.dark_id,
            "white_id": self.white_id,
            
            # Units
            "wavelength_unit": "nm",
            "absorbance_unit": "AU",
        }
        
        # Add MOF recipe if available for this well
        if well_id in self.well_recipes:
            recipe = self.well_recipes[well_id]
            base_meta.update({
                "metal_precursor_name": recipe["metal_precursor"]["name"],
                "metal_precursor_vol_ml": recipe["metal_precursor"]["volume_ml"],
                "organic_precursor_name": recipe["organic_precursor"]["name"], 
                "organic_precursor_vol_ml": recipe["organic_precursor"]["volume_ml"],
                "solvent_name": recipe["solvent"]["name"],
                "solvent_vol_ml": recipe["solvent"]["volume_ml"],
                "total_volume_ml": recipe["total_volume_ml"]
                # "molar_ratio": recipe["molar_ratio"],
                # "preparation_time": recipe["preparation_time"]
            })
        
        # Add any additional metadata
        if additional_meta:
            base_meta.update(additional_meta)
        
        return base_meta
    
    # ------------------------- Device Management -------------------------------------------------------------------
    def find_spectrometers(self):
        """Probe and return list of device IDs."""
        count = self.ocean.find_devices()
        if count == 0:
            raise RuntimeError("No Ocean Insight Spectrometers Detected")
        
        return self.ocean.get_device_ids() 
    
    def open_spectrometer(self):
        device_ids = self.find_spectrometers()
        self.device_id = device_ids[0]
        self.spectrometer = self.ocean.open_device(self.device_id)
        
        print(f"Opened Spectrometer {self.device_id}")
        return self.spectrometer, self.device_id
        
    def close_spectrometer(self):
        self.ocean.close_device(self.device_id)
        print(f"Closed Spectrometer {self.device_id}")
        
    # ---------------------Spectrometer Configuration-------------------------------------------------------
    
    def configure_device(self, 
                       integration_time_us : int = 50000, 
                       scans_to_avg : int = 10,
                       boxcar_width : int = 10):
        """Configure spectrometer acquisition parameters."""
        self.spectrometer.set_integration_time(integration_time_us)
        self.spectrometer.set_scans_to_average(scans_to_avg)
        self.spectrometer.set_boxcar_width(boxcar_width)
    
    def lamp_shutter(self, open: bool = False):
        """Open/close internal lamp shutter if the device supports it."""
        
        state = "Open" if open else "Close"
        try:
            self.spectrometer.Advanced.set_enable_lamp(open)
            if self.spectrometer.Advanced.get_enable_lamp() == open:
                print(f"Light shutter set to {state}")
                
        except AttributeError:
            print("This spectrometer has no controllable lamp shutter")
        
    
    # -------------------------- RAW ACQUISITION -----------------------#
    def measure_raw_spectrum(self):
        """Acquire raw spectrum from spectrometer."""
        wl = np.array(self.spectrometer.get_wavelengths())
        vals = np.array(self.spectrometer.get_formatted_spectrum())
        
        return wl, vals
    
    @staticmethod
    def compute_absorbance(sample_vals: np.ndarray, 
                           dark_vals : np.ndarray, 
                           white_vals : np.ndarray,
                           eps : float = 1e-9):
        """
        Compute absorbance spectrum from raw intensities:
        A(λ) = -log10( (I_sample - I_dark) / (I_white - I_dark) )
        """
        # dark-correct
        sample_dc = sample_vals - dark_vals
        white_dc  = white_vals  - dark_vals
        ratio     = sample_dc / (white_dc + eps)
        ratio = np.maximum(ratio, 1e-9)
         
        return -np.log10(ratio)
    
    
    #---------------------------------------- CSV Helper ----------------------------------------#
    
    def _csv_path(self, well_id : str | np.str_) -> pathlib.Path:
        """Get path for CSV file for a specific well."""
        return self.plate_dir / f"{str(well_id)}.csv"
    
    def _ensure_file(self, path: pathlib.Path, well_id: str, meta: Dict[str, object] = None):
        """Create CSV file with comprehensive header if it does not exist."""
        if path.exists():
            return
        
        # Generate comprehensive metadata including MOF recipe
        enhanced_meta = self._get_enhanced_metadata(well_id, meta or {})
        
        # Create file with YAML header and CSV column headers
        header_content = _yaml_header(enhanced_meta) 
        path.write_text(header_content)
    
    @staticmethod
    def read_spectrum_csv(path: str | pathlib.Path) -> Tuple[Dict[str, str], pd.DataFrame]:
        """Return metadata dict *and* absorbance DataFrame."""
        p = pathlib.Path(path)
        meta, _ = _parse_header(p)
        df = pd.read_csv(p, comment="#", index_col="wavelength_nm", encoding="cp1252")
        return meta, df
   
    
    # -------------------------CSV append ------------------------------------#
    
    def _append_spectrum_csv(self,
        well_id: str | np.str_,
        time_min: int,
        wl: np.ndarray,
        absorbance: np.ndarray,
        meta: Dict[str, object] = None,
    ) -> None:
        """Append spectrum data to CSV file."""
        
        path = self._csv_path(well_id)
        base = f"{time_min} min"

        # 1) Read existing file or create new DataFrame
        if path.exists():
            header_dict, header_lines = _parse_header(path)
            df = pd.read_csv(path, comment="#", index_col="wavelength_nm", encoding="cp1252")
        else:
            # Create new file with comprehensive metadata
            self._ensure_file(path, well_id, meta)
            df = pd.DataFrame(index=np.round(wl, 1))

        # 2) Add new time column
        if base not in df.columns:
            col_name = base
        else:
            pattern = re.compile(rf"^{re.escape(base)}-(\d+)$")
            existing = [int(m.group(1)) for c in df.columns if (m := pattern.match(c))]
            next_k = (max(existing) + 1) if existing else 2
            col_name = f"{base}-{next_k}"
        df[col_name] = absorbance

        # 3) Write back to file (preserving header)
        if path.exists():
            # Keep existing header, just update data
            header_dict, header_lines = _parse_header(path)
            header_content = _yaml_header(header_dict)
        else:
            # This shouldn't happen since we called _ensure_file above
            enhanced_meta = self._get_enhanced_metadata(well_id, meta or {})
            header_content = _yaml_header(enhanced_meta)

        # Write header + data
        with path.open("w") as f:
            f.write(header_content)
            f.write("wavelength_nm," + ",".join(df.columns) + "\n")
            df.to_csv(f, header=False, lineterminator= "\n")

    # ------------------------- Move Spectrometer ---------------------------------- #
    
    @requires_active_tool
    def position_probe(self,
                       location: Union[Well, Tuple, Location]) -> None:
        """Move the spectrometer probe above `location` and update `self.current_well`."""

        # Error handling to check if the labware at location has lid or not
        self.lid_on_top_error_handling(location, expected_condition = False)

        x, y, z = Labware._getxyz(location)

        self._machine.safe_z_movement()
        self._machine.move_to(x=x, y=y, wait = True)
        self._machine.move_to(z=z, wait = True)
        
        # ---------- robust well bookkeeping ----------
        if isinstance(location, Well):
            self.current_location = location
            self.current_well = location.name
        elif isinstance(location, Location):
            # Opentrons-style Location → Well
            self.current_location = location._labware
            self.current_well = self.current_location.name
        else:
            self.current_well = location                     # e.g. raw (x, y, z) tuple  


    @requires_active_tool
    def wash_probe(self, wash_loc : Union[Well, Tuple, Location], n_cycles : int = 2):
        """Wash the probe with the supplied location."""
        for i in range(n_cycles):
            self.position_probe(wash_loc)


    @requires_active_tool
    def collect_spectrum(self,
                         location: Union[Well, Tuple, Location],
                         elapsed_min : int,
                         open : bool | None = None,
                         save: bool = False): 
        """Collect spectrum at specified location and optionally save to CSV."""

        self.position_probe(location)
        
        if self.dark is None  or self.white is None:
            raise RuntimeError("Dark/white spectra not set")
        
        # Lamp Shutter control, if desired
        if open is not None: # only act when caller says True/False
            self.lamp_shutter(open = open) 
        
        # Acquire
        wl, vals = self.measure_raw_spectrum()
        
        absorbance = self.compute_absorbance(vals, self.dark, self.white)
        
        # --------save ------------
        if save:
            loc = self.current_location
            well_id = str(self.current_well) 
            meta = dict(
                well_id = well_id,
                slot= loc,
                pixels = len(wl),
                integration_time_us = self.spectrometer.get_integration_time(),
                dark_id = self.dark_id,
                white_id = self.white_id,
                wavelength_unit = "nm",
                absorbance_unit = "a.u.",
            )
            self._append_spectrum_csv(
                well_id, elapsed_min, wl, absorbance, meta
            )
        
        return wl, vals, absorbance

    
    #------------------ Reference Spectrum Setup with recall ----------------------#
    def _latest_ref(self, prefix: str) -> pathlib.Path | None:
        """Return Path to the newest '<prefix>_YYYYmmdd_HHMMSS.npy' file."""
        files = sorted(self.refs_dir.glob(f"{prefix}_*.npy"))
        return files[-1] if files else None

    def set_dark(self, n_avg: int = 10):
        """Capture dark spectrum; store with an ID timestamp."""
        wl, vals = self.measure_raw_spectrum()
        if n_avg > 1:
            acc = [self.measure_raw_spectrum()[1] for _ in range(n_avg-1)]
            vals = np.mean([vals]+acc, axis=0)
        dark_data = np.column_stack((wl, vals))
        self.dark = vals
        self.dark_id  = f"dark_{dt.datetime.now():%Y%m%d_%H%M%S}"
        self._dark_path = self.refs_dir / f"{self.dark_id}.npy"
        np.save(self._dark_path, dark_data)
        return self.dark_id

    def set_white(self, n_avg: int = 10):
        """Capture white (reference) spectrum; store with an ID timestamp."""
        wl, vals = self.measure_raw_spectrum()
        if n_avg > 1:
            acc = [self.measure_raw_spectrum()[1] for _ in range(n_avg-1)]
            vals = np.mean([vals]+acc, axis=0)
        white_data = np.column_stack((wl, vals))
        self.white = vals
        self.white_id = f"white_{dt.datetime.now():%Y%m%d_%H%M%S}"
        self._white_path = self.refs_dir / f"{self.white_id}.npy"
        np.save(self._white_path, white_data)
        return self.white_id
    

    def _load_references(self):
        """
        Populate self.dark/white and their IDs from disk **if possible**.

        Priority:
        (i) newest   dark_<timestamp>.npy / white_<timestamp>.npy pair  
        (ii) legacy  dark.npy / white.npy       (keeps old projects usable)
        """
        dark_path  = self._latest_ref("dark")
        white_path = self._latest_ref("white")

        # --- newest scheme present ---
        if dark_path and white_path:
            arr_d = np.load(dark_path)
            self.dark = arr_d[:,1] if arr_d.ndim == 2 else arr_d

            arr_w = np.load(white_path)
            self.white = arr_w[:,1] if arr_w.ndim == 2 else arr_w
        #     self.dark      = np.load(dark_path)
        #     self.white     = np.load(white_path)
            self.dark_id   = dark_path.stem        # e.g. 'dark_20250528_154722'
            self.white_id  = white_path.stem
            self._dark_path, self._white_path = dark_path, white_path
            return 
        
        # --- fallback to legacy fixed names ---
        legacy_d = self._dark_path # points to e.g. dark.npy (or whatever ref_dark was)
        legacy_w = self._white_path # points to e.g. white.npy
        
        if legacy_d.exists() and legacy_w.exists():
            arr_d = np.load(legacy_d)
            self.dark = arr_d[:,1] if arr_d.ndim == 2 else arr_d

            arr_w = np.load(legacy_w)
            self.white = arr_w[:,1] if arr_w.ndim == 2 else arr_w
        #     self.dark      = np.load(legacy_d)
        #     self.white     = np.load(legacy_w)
            self.dark_id   = legacy_d.stem
            self.white_id  = legacy_w.stem


    
    
    # ---------------------------------------------------- Storage management -
    def set_storage_root(self, new_root: str | pathlib.Path) -> None:
        """Change where all subsequent spectra are written."""
        self.base_dir = pathlib.Path(new_root).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        self.refs_dir = self.base_dir / "references"
        self.refs_dir.mkdir(exist_ok = True)
        
        self.plate_dir = self.base_dir / self.plate_id
        self.plate_dir.mkdir(parents=True, exist_ok=True)
        

    def plot_spectrum(self, 
                  location: Union[Well, Tuple, Location], 
                  elapsed_min: int = 15,
                  save_plot: bool = False,
                  show_plot: bool = True,
                  figsize: Tuple[int, int] = (10, 6)) -> Tuple[np.ndarray, np.ndarray]:
        """Plot absorbance spectrum for a given location/well."""

        # Determine well_id from location
        if isinstance(location, Well):
            well_id = location.name
        elif isinstance(location, Location):
            well_id = location._labware.name
        else:
            well_id = str(location)

        # Check if CSV file exists
        csv_path = self._csv_path(well_id)
        if not csv_path.exists():
            raise FileNotFoundError(
                f"No spectrum data found for {well_id}. "
                f"Expected file: {csv_path}. "
                f"Run collect_spectrum() first."
            )

        # Read the CSV data
        try:
            meta, df = self.read_spectrum_csv(csv_path)
        except Exception as e:
            raise RuntimeError(f"Failed to read spectrum data from {csv_path}: {e}")

        # Check if the requested time point exists
        base = f"{elapsed_min} min"
        pattern = re.compile(rf"^{re.escape(base)}(?:-(\d+))?$")
        matches = []
        for c in df.columns:
            m = pattern.match(c)
            if m:
                k = int(m.group(1)) if m.group(1) else 1
                matches.append((k, c))
        if not matches:
            raise ValueError(f"No spectra data found for {well_id} at {elapsed_min} min.")
            
        _, time_col = max(matches, key=lambda x: x[0])

        # Extract wavelengths and absorbance (already computed)
        wavelengths = df.index.values  # wavelength_nm is the index
        absorbance = df[time_col].values

        # Create the plot
        if show_plot or save_plot:
            plt.figure(figsize=figsize)
            plt.plot(wavelengths, absorbance, color='purple', linewidth=2, label='Absorbance')
            plt.xlabel("Wavelength (nm)", fontsize=12)
            plt.xlim(380, 800)
            plt.ylabel("Absorbance (a.u.)", fontsize=12)
            plt.ylim(-0.1, 1.2)
            plt.title(f"Absorbance Spectrum - {well_id} ({time_col})", fontsize=14)
            plt.grid(True, alpha=0.3)
            plt.legend()
            plt.tight_layout()

            # Save plot if requested
            if save_plot:
                safe_col = time_col.replace(" ","").replace("-","_")
                plot_path = csv_path.parent / f"{well_id}_{safe_col}_absorbance.png"
                plt.savefig(plot_path, dpi=300, bbox_inches='tight')
                print(f"Plot saved to: {plot_path}")

            if show_plot:
                plt.show()
            else:
                plt.close()

        return wavelengths, absorbance