"""Execute autonomous_mof_synthesis.ipynb offline, end to end, with fake_axo hardware stand-ins.

usage (from Code/):
    python tests/notebook_dry_run.py autonomous_mof_synthesis.ipynb dry_run_out [--truth fast|default]
                                     [--rounds 4] [--reagents 40] [--resume]

--resume runs the notebook again in a new kernel on the same <out> with RESUME = True (a kernel restart
in the middle of a campaign: the stand-in deck starts with all lids in slot 4 and empty syringes).

Every notebook cell runs unchanged except: the spectrum/output folders point into <out>, a first cell
installs the stand-ins, and after the Run cell an "operator" refills the reservoirs whenever the run
pauses with paused_refill_reagents. Writes <out>/executed.ipynb and <out>/dry_run_report.json and
prints, per round: level, vials, reading times, alpha(t1), next level, lid/tool problems and prompts.
"""
import argparse
import json
import os
import re
import shutil
from pathlib import Path

import nbformat
from nbclient import NotebookClient

HERE = Path(__file__).resolve().parent

ap = argparse.ArgumentParser()
ap.add_argument("notebook", type=Path)
ap.add_argument("out", type=Path)
ap.add_argument("--src", type=Path, default=None, help="Code/src to test (default: next to the notebook)")
ap.add_argument("--truth", default="fast", choices=["fast", "default"])
ap.add_argument("--rounds", type=int, default=4)
ap.add_argument("--reagents", type=float, default=40.0)
ap.add_argument("--resume", action="store_true")
ap.add_argument("--in-process", action="store_true", help="Execute cells with IPython in this process, without starting a networked Jupyter kernel.")
args = ap.parse_args()

src = (args.src or args.notebook.resolve().parent / "src").resolve()
out = args.out.resolve()
work = out / "Code"
work.mkdir(parents=True, exist_ok=True)
if (work / "src").exists():
    shutil.rmtree(work / "src")
shutil.copytree(src, work / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))  # copy: works on Windows too

nb = nbformat.read(args.notebook, as_version=4)
cells = nb.cells
setup = nbformat.v4.new_code_cell(
    "import sys, os\n"
    "sys.path.insert(0, os.path.abspath('src'))\n"
    f"sys.path.insert(0, {str(HERE)!r})\n"
    "import fake_axo\n"
    f"FA_EVENTS = fake_axo.install(truth={args.truth!r}, n_lids=4)\n"
    "print('[dry run] hardware stand-ins installed')")

config_i = next(i for i, c in enumerate(cells) if c.cell_type == "code" and "OUTPUT_DIR =" in c.source)
s = cells[config_i].source
s = re.sub(r'SPECTRUM_BASE_DIR = .*', f'SPECTRUM_BASE_DIR = {str(out / "spectrum_data")!r}', s)
s = re.sub(r'OUTPUT_DIR = .*', f'OUTPUT_DIR = {str(out / "optimization_results")!r}', s)
s = re.sub(r'N_ROUNDS = \d+', f'N_ROUNDS = {args.rounds}', s)
s = re.sub(r'REAGENTS_ML = \{.*\}', f'REAGENTS_ML = {{"metal": {args.reagents}, "organic": {args.reagents}, "solvent": {args.reagents}}}', s)
if args.resume:
    s = s.replace("RESUME = False", "RESUME = True")
cells[config_i].source = s

op_i = next(i for i, c in enumerate(cells) if c.cell_type == "code" and "DISCARD = False" in c.source)
operator = nbformat.v4.new_code_cell(
    "while result['status'] == 'paused_refill_reagents':\n"
    "    print('[dry run] operator refills:', result['message'])\n"
    f"    loop.refill_reagents(metal={args.reagents}, organic={args.reagents}, solvent={args.reagents})\n"
    "    result = loop.run()\n"
    "print('[dry run] final status:', result['status'], '-', result['message'])")
report = nbformat.v4.new_code_cell(r"""
import json
from science_jubilee.analysis.gualtieri import parse_times_from_operations_log
rep = {"rounds": [], "events": FA_EVENTS, "status": result["status"], "message": result["message"],
       "lids_end": {s: l for s, l in fake_axo.STATE["machine"].lids.items() if l}, "sim_end": str(fake_axo.Clock.t)}
for _, d in pd.read_csv(loop.output_dir / "decisions.csv").iterrows():
    plan = json.loads(next((loop.output_dir / "plans").glob(f"batch_{int(d.iteration):02d}_*.json")).read_text())
    slot = [k for k in plan if k.startswith("slot")][0]
    times = {w: [round(v, 2) for _, v in sorted(parse_times_from_operations_log(str(Path(d.batch_dir) / "operations_log.txt"), w, int(slot[4:])).items())]
             for w in plan[slot]}
    rep["rounds"].append({"round": int(d.iteration), "level": int(d.phase), "slot": slot, "wells": list(plan[slot]),
                          "volumes": {w: [v["metal_precursor_vol"], v["organic_precursor_vol"], v["solvent_vol"]] for w, v in plan[slot].items()},
                          "interval_min": plan["spectrum_record_interval_mins"], "readings": plan["max_spectrum_records"],
                          "max_alpha_t1": None if pd.isna(d.max_alpha_t1) else float(d.max_alpha_t1),
                          "next_level": int(d.next_phase), "k_n": d.k_n_batch, "reading_times_min": times,
                          "reagents_left": d.get("reagents_left_ml"),
                          "log": {k: (Path(d.batch_dir) / "operations_log.txt").read_text().count(v) for k, v in {
                              "dark/white recorded": "Recording dark reference",
                              "lids moved to slot 4 at round start": "and Placed Lid in Slot 4",
                              "dual syringe emptied": "Dual Syringe Drive 0 reset",
                              "dual syringe kept filled": "Dual Syringe kept filled",
                              "single syringe emptied": "Single Syringe reset",
                              "lids put on (rack)": "Put Lid to Samples",
                              "precursor lid put on": "and Placed Lid in Slot 1"}.items()}})
Path(OUTPUT_DIR, "..", "dry_run_report.json").resolve().write_text(json.dumps(rep, indent=1, default=str))
""")
nb.cells = [setup] + cells[:op_i + 1] + [operator] + cells[op_i + 1:] + [report]

try:
    if args.in_process:
        from IPython.terminal.interactiveshell import TerminalInteractiveShell
        from IPython.utils.capture import capture_output

        shell = TerminalInteractiveShell.instance(simple_prompt=True)
        original_directory = Path.cwd()
        try:
            os.chdir(work)
            for cell in nb.cells:
                if cell.cell_type != "code":
                    continue
                with capture_output(stdout=True, stderr=True, display=True) as captured:
                    executed = shell.run_cell(cell.source, store_history=True)
                cell.execution_count = shell.execution_count - 1
                cell.outputs = []
                for stream_name in ("stdout", "stderr"):
                    value = getattr(captured, stream_name)
                    if value:
                        cell.outputs.append(nbformat.v4.new_output("stream", name=stream_name, text=value))
                for value in captured.outputs:
                    cell.outputs.append(nbformat.v4.new_output("display_data", data=value.data,
                                                               metadata=value.metadata))
                error = executed.error_before_exec or executed.error_in_exec
                if error is not None:
                    raise error
        finally:
            os.chdir(original_directory)
    else:
        client = NotebookClient(nb, timeout=7200, kernel_name="python3", resources={"metadata": {"path": str(work)}})
        client.execute()
finally:
    nbformat.write(nb, out / ("executed_resume.ipynb" if args.resume else "executed.ipynb"))

rep = json.loads((out / "dry_run_report.json").read_text())
problems = [e for e in rep["events"] if e[1] not in ("OPERATOR_PROMPT", "SETUP_PROMPT")]
for r in rep["rounds"]:
    a = r["max_alpha_t1"]
    print(f"\nRound {r['round']} | level {r['level']} | {r['slot']} {r['wells']} | every {r['interval_min']} min x {r['readings']} | "
          f"max alpha(t1) = {'n/a' if a is None else round(a, 3)} -> next level {r['next_level']}")
    print(f"   Co/2-mIm/TEA (mL): {r['volumes']}")
    print(f"   k_n: {r['k_n']}")
    for w, t in r["reading_times_min"].items():
        print(f"   {w} read at (min): {t}")
    print(f"   reagents left after round: {r['reagents_left']}")
    print(f"   operations log: {r['log']}")
print(f"\nfinal status: {rep['status']} ({rep['message']})")
print("operator prompts:", [e for e in rep["events"] if e[1] == "OPERATOR_PROMPT"])
print("lid/tool problems:", problems or "none")
print("lids at the end:", rep["lids_end"], "| simulated clock at the end:", rep["sim_end"])

# Fail the software check if the integrated notebook does not complete cleanly.
assert rep["status"] == "completed", rep["status"]
assert len(rep["rounds"]) == args.rounds, rep["rounds"]
assert not problems, problems
for round_result in rep["rounds"]:
    level = round_result["level"]
    alpha = round_result["max_alpha_t1"]
    fast = alpha is not None and alpha > 0.5
    expected = ({0: 2, 1: 2, 2: 3, 3: 3} if fast else {0: 1, 1: 1, 2: 2, 3: 3})[level]
    assert round_result["next_level"] == expected, round_result
print("PASS: notebook completion, device-event checks and configured alpha-driven decisions.")
