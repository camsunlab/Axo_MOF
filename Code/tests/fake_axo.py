"""Stand-ins for the Axo hardware so that run_staged_bo_60min.ipynb can be executed offline.

install() replaces only: the Duet/HTTP machine (science_jubilee.Machine.Machine), the OceanDirect SDK,
the wall clock and time.sleep inside Experiment, and input(). Everything else - Experiment, Syringe,
DoubleSyringe, VacuumGripper, Spectrometer, Labware, the Gualtieri fit, the GP/EI selection and
ReactionAwareBOLoop - is the real code. Robot operation times are calibrated on the recorded BO-6
operations log (tool pick-up ~10 s, 3 spectra ~25 s, probe wash ~20 s, one lid move ~18 s).

The spectra come from a synthetic composition -> kinetics map (simulation.virtual_lab.GroundTruth);
they exercise the code paths and say nothing about real ZIF-67 kinetics. Lids are modelled
physically (a LIFO stack in slot 4); lid or tool mistakes are recorded in EVENTS.
"""
import builtins
import json
import sys
import types
from datetime import datetime, timedelta

import numpy as np

EVENTS = []


class Clock:
    t = datetime(2026, 11, 3, 9, 0, 0)


def advance(sec):
    Clock.t += timedelta(seconds=float(sec))


class FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return Clock.t


def event(kind, detail=""):
    EVENTS.append((Clock.t.strftime("%H:%M:%S"), kind, detail))


TRUTHS = {"default": {}, "fast": dict(a_min=3.0, a_curvature=14.0, b_offset=1.0, b_slope=0.2,
                                       k_g_offset=0.15, k_g_slope=0.05)}
STATE = {"device": None, "machine": None}


def install(truth="fast", n_lids=4, quiet=True):
    """Patch the hardware layer; call after Code/src is on sys.path and before the notebook imports it."""
    from science_jubilee.simulation.virtual_lab import GroundTruth, WAVELENGTHS, BASELINE, BAND_400, BAND_590
    from science_jubilee.analysis.gualtieri import gualtieri_model

    gt = GroundTruth(**TRUTHS[truth])
    rng = np.random.default_rng(0)
    dark, white = 1000.0, 41000.0

    class FakeDevice:
        def __init__(self):
            self.light_on, self.spec, self.t0 = True, None, {}

        def get_wavelengths(self):
            return WAVELENGTHS.tolist()

        def get_formatted_spectrum(self):
            advance(4)
            if not self.light_on:
                return np.full(WAVELENGTHS.size, dark).tolist()
            spec = self.spec
            well = spec.current_location
            A = 0.5 * BASELINE
            name = getattr(well, "name", None)
            if getattr(well, "slot", None) in (2, 5) and name in spec.well_recipes:
                r = spec.well_recipes[name]
                x = (r["metal_precursor"]["volume_ml"], r["organic_precursor"]["volume_ml"], r["solvent"]["volume_ml"])
                t0 = self.t0.setdefault((str(spec.experiment_dir), name), Clock.t)
                t = (Clock.t - t0).total_seconds() / 60.0
                p = gt.parameters(x)
                prog = float(gualtieri_model(np.array([t]), p["k_g"], p["a"], p["b"], 1.0)[0])
                A = BASELINE + prog * (p["I_max"] * BAND_400 + p["feature_amplitude"] * BAND_590) \
                    + rng.normal(0, .002, WAVELENGTHS.size)
            return (dark + (white - dark) * 10 ** (-A)).tolist()

        def set_integration_time(self, v): pass
        def get_integration_time(self): return 50000
        def set_scans_to_average(self, v): pass
        def set_boxcar_width(self, v): pass

    device = FakeDevice()
    STATE["device"] = device

    class OceanDirectAPI:
        def find_devices(self): return 1
        def get_device_ids(self): return [7]
        def open_device(self, i): return device
        def close_device(self, i): pass

    odapi = types.ModuleType("oceandirect.OceanDirectAPI")
    odapi.OceanDirectAPI, odapi.OceanDirectError = OceanDirectAPI, type("OceanDirectError", (Exception,), {})
    sys.modules["oceandirect"], sys.modules["oceandirect.OceanDirectAPI"] = types.ModuleType("oceandirect"), odapi

    import science_jubilee.Machine as M
    import science_jubilee.Experiment as EXP
    import science_jubilee.tools.Oceandirect_axo as OD
    from science_jubilee.decks.Deck import Deck
    from science_jubilee.labware.Labware import Labware, Location, Well

    EXP.datetime = FakeDatetime
    EXP.time = types.SimpleNamespace(sleep=advance)
    _spec_init = OD.Spectrometer.__init__

    def spec_init(self, *a, **k):
        _spec_init(self, *a, **k)
        device.spec = self
    OD.Spectrometer.__init__ = spec_init

    vac = {0: (84, 51), 1: (225, 57), 2: (84, 147), 3: (225, 149), 4: (79, 244), 5: (221, 244)}
    real_getxyz = Labware._getxyz

    def tracking_getxyz(location):
        x, y, z = real_getxyz(location)
        m = STATE["machine"]
        if m is not None:
            if isinstance(location, Well):
                m.target_slot = location.slot
            elif isinstance(location, Location):
                m.target_slot = location._labware.slot
            else:
                m.target_slot = min(vac, key=lambda s: (vac[s][0] - x) ** 2 + (vac[s][1] - y) ** 2)
        return x, y, z
    Labware._getxyz = staticmethod(tracking_getxyz)

    class FakeMachine(M.Machine):
        EXTRUDERS = {0: [0], 2: [1, 2], 3: [], 4: []}

        def __init__(self, *args, **kwargs):
            self.deck, self.tools, self._active = None, {}, -1
            self.pos = {"X": 0.0, "Y": 0.0, "Z": 250.0, "U": 0.0, "E0": 0.0, "E1": 0.0, "E2": 0.0}
            self.target_slot, self.vacuum, self.carrying = None, False, None
            self.lids = {s: [] for s in range(6)}
            self.lids[4] = [f"lid{i + 1}" for i in range(n_lids)]
            STATE["machine"] = self

        @property
        def active_tool_index(self):
            return self._active

        @property
        def tool_z_offsets(self):
            return {i: 0 for i in range(6)}

        def home_all(self):
            advance(60)

        def load_deck(self, name, path=None):
            self.deck = Deck(name)
            return self.deck

        def load_tool(self, tool):
            self.tools[tool.index] = {"name": tool.name, "tool": tool}
            tool._machine = self
            tool.post_load()
            tool.tool_offset = 0

        def pickup_tool(self, tool):
            idx = tool if isinstance(tool, int) else tool.index
            if self._active != -1:
                event("PICKUP_WHILE_HOLDING", f"T{idx} while T{self._active} active")
                self.tools[self._active]["tool"].is_active_tool = False
            advance(10)
            self._active = idx
            self.tools[idx]["tool"].is_active_tool = True

        def park_tool(self):
            advance(9)
            if self._active in self.tools:
                self.tools[self._active]["tool"].is_active_tool = False
            self._active = -1

        def get_position(self):
            return {k: f"{v:.3f}" for k, v in self.pos.items()}

        def safe_z_movement(self):
            self.pos["Z"] = 120.0

        def _set_absolute_positioning(self): pass
        def _set_relative_positioning(self): pass
        def _set_absolute_extrusion(self): pass
        def _set_relative_extrusion(self): pass

        def _apply_e(self, e0, e1, relative, s):
            drives, dist = self.EXTRUDERS.get(self._active, []), 0.0
            for val, k in ((e0, 0), (e1, 1)):
                if val is None:
                    continue
                if k >= len(drives):
                    event("EXTRUDER_MOVE_WITHOUT_TOOL", f"T{self._active}")
                    continue
                d = f"E{drives[k]}"
                new = self.pos[d] + val if relative else val
                dist, self.pos[d] = max(dist, abs(new - self.pos[d])), new
            if dist and s:
                advance(dist / float(s) * 60.0)

        def move_to(self, x=None, y=None, z=None, e0=None, e1=None, v=None, s=6000, param=None, wait=False):
            if x is not None: self.pos["X"] = float(x)
            if y is not None: self.pos["Y"] = float(y)
            if z is not None:
                self.pos["Z"] = float(z)
                if self._active in (0, 2, 3) and self.target_slot is not None and self.lids.get(self.target_slot):
                    event("TOOL_LOWERED_ONTO_LID", f"T{self._active} into slot {self.target_slot}")
            advance(2.5)
            self._apply_e(e0, e1, False, s)

        def move(self, dx=0, dy=0, dz=0, de0=None, de1=None, dv=0, s=6000, param=None, wait=False):
            self.pos["X"] += dx; self.pos["Y"] += dy; self.pos["Z"] += dz
            self._apply_e(de0, de1, True, s)

        def gcode(self, cmd="", timeout=None, response_wait=30):
            if cmd.startswith('M409 K"tools[]"'):
                return json.dumps({"result": [{"number": i, "extruders": e} for i, e in self.EXTRUDERS.items()]})
            if cmd.startswith("M42"):
                on = not cmd.rstrip().endswith("S0")
                if not on:
                    if self.carrying == "NOTHING":
                        event("DROP_WITHOUT_LID", f"software now thinks slot {self.target_slot} is covered")
                    elif self.carrying:
                        self.lids[self.target_slot].append(self.carrying)
                    self.carrying = None
                self.vacuum = on
            elif cmd.startswith("G30"):
                advance(7)
                if self.vacuum and self.carrying is None:
                    if self.lids[self.target_slot]:
                        self.carrying = self.lids[self.target_slot].pop()
                    else:
                        self.carrying = "NOTHING"
                        event("GRIP_EMPTY", f"no lid at slot {self.target_slot}")
            return "ok"

    M.Machine = FakeMachine

    last_error = [""]
    real_print = builtins.print

    def quiet_print(*a, **k):
        s = " ".join(str(x) for x in a)
        if s.startswith("Error :"):
            last_error[0] = s
        if quiet and (s.startswith("Plot saved") or (s.startswith("[") and "] " in s[:25])):
            return
        real_print(*a, **k)

    def fake_input(prompt=""):
        if "Press any key" in prompt:
            device.light_on = not device.light_on
            event("OPERATOR_PROMPT", "probe light " + ("off" if not device.light_on else "on"))
            return ""
        event("BLOCKING_PROMPT", prompt.strip() + " | " + last_error[0])
        return "y"

    # Module globals shadow the builtins (ipykernel re-installs builtins.input before every cell)
    import science_jubilee.tools.Tool as T
    import science_jubilee.labware.Labware as LW
    for mod in (EXP, T, OD):
        mod.input, mod.print = fake_input, quiet_print

    def offset_prompt(prompt=""):
        event("SETUP_PROMPT", "load_manualOffset asked classic/rigid; answered 2")
        return "2"
    LW.input = offset_prompt
    return EVENTS
