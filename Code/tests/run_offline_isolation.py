"""Exercise the full demo with network connections and hardware imports blocked."""
import argparse
import json
from pathlib import Path

import _offline_guard


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("offline_demo_outputs"))
    args = parser.parse_args()
    _offline_guard.install()
    from science_jubilee.offline_demo import run_demo, default_data_dir, verify_input_files
    before = verify_input_files(default_data_dir())
    summary = run_demo(output_dir=args.output)
    after = verify_input_files(default_data_dir())
    assert before == after, "Input data changed during the run."
    loaded = _offline_guard.loaded_hardware_modules()
    assert not loaded, loaded
    assert summary["decision"]["executed"] is False
    result = {"network_connections_blocked": True, "hardware_imports_blocked": True,
              "hardware_modules_loaded": loaded, "input_hashes_unchanged": True,
              "physical_experiments_executed": False, "result": "passed"}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "isolation_check.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
