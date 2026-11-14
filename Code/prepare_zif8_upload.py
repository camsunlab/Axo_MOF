"""Stage the four recorded ZIF-8 batches for upload, or check a deposited archive.

This utility copies source files without modifying their contents. It does not
run the robot, fit kinetics, or regenerate scientific results.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil

ARCHIVE = Path("Dataset") / "ZIF8_20261104"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def recorded_batches(root: Path) -> list[dict]:
    records = root / ARCHIVE / "run_records"
    with (records / "decisions.csv").open(encoding="utf-8-sig", newline="") as stream:
        decisions = list(csv.DictReader(stream))
    with (records / "vials.csv").open(encoding="utf-8-sig", newline="") as stream:
        vials = list(csv.DictReader(stream))
    if len(decisions) != 4 or len(vials) != 11:
        raise ValueError("Expected the recorded four-round, eleven-vial ZIF-8 campaign.")
    batches = []
    for row in decisions:
        name = PureWindowsPath(row["batch_dir"]).name
        wells = row["wells"].split()
        if len(wells) != int(row["n_vials"]):
            raise ValueError(f"Inconsistent well count for {name}")
        batches.append({"name": name, "wells": wells, "round": int(row["iteration"])})
    return batches


def required_files(batch: Path, wells: list[str]) -> list[Path]:
    paths = [batch / name for name in
             ("mof_recipes.json", "experiment_metadata.json", "operations_log.txt")]
    paths += [batch / "spectra" / f"{well}.csv" for well in wells]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError("Missing recorded files:\n" + "\n".join(missing))
    refs = sorted((batch / "references").glob("*.npy"))
    for prefix in ("dark", "white"):
        if not any(p.stem == prefix or p.stem.startswith(prefix + "_") for p in refs):
            raise FileNotFoundError(f"Missing {prefix} reference in {batch / 'references'}")
    recipes = json.loads((batch / "mof_recipes.json").read_text(encoding="utf-8-sig"))
    if not set(wells).issubset(recipes):
        raise ValueError(f"Missing recorded recipes in {batch}")
    json.loads((batch / "experiment_metadata.json").read_text(encoding="utf-8-sig"))
    return paths + refs


def stage(source: Path, output: Path, root: Path) -> None:
    source, output = source.resolve(), output.resolve()
    if output == source or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("Choose an upload output folder separate from the original data folder.")
    selected = []
    batches = recorded_batches(root)
    # Validate every batch before creating or copying any output file.
    for batch in batches:
        directory = source / batch["name"]
        for path in required_files(directory, batch["wells"]):
            relative = ARCHIVE / "raw" / batch["name"] / path.relative_to(directory)
            destination = output / relative
            digest = sha256(path)
            if destination.exists() and sha256(destination) != digest:
                raise FileExistsError(f"Different file already exists: {destination}")
            selected.append((path, relative, digest))
    for path, relative, digest in selected:
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(path, destination)
        if sha256(destination) != digest:
            raise RuntimeError(f"Copy verification failed: {relative}")
    manifest = {"scope": "Recorded ZIF-8 input files copied without changing their contents; not a fit-validation report.",
                "rounds": 4, "vials": 11,
                "files": [{"path": relative.as_posix(), "sha256": digest,
                           "size_bytes": path.stat().st_size} for path, relative, digest in selected]}
    manifest_path = output / ARCHIVE / "raw" / "raw_data_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(selected)} unchanged input files plus one manifest.")
    print(f"Upload this folder into the repository root: {output / 'Dataset'}")
    print("Original source files were not changed. Generated PNG plots were not copied.")


def check(root: Path) -> None:
    root = root.resolve()
    expected = set()
    for batch in recorded_batches(root):
        directory = root / ARCHIVE / "raw" / batch["name"]
        expected.update(p.relative_to(root).as_posix()
                        for p in required_files(directory, batch["wells"]))
    manifest_path = root / ARCHIVE / "raw" / "raw_data_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest["files"]
    if {entry["path"] for entry in entries} != expected or len(entries) != len(expected):
        raise ValueError("Raw-data manifest does not match the required campaign files.")
    for entry in entries:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to((root / ARCHIVE / "raw").resolve()):
            raise ValueError("Manifest path is outside the raw-data archive.")
        if path.stat().st_size != entry["size_bytes"] or sha256(path) != entry["sha256"]:
            raise ValueError(f"Raw-data checksum mismatch: {entry['path']}")
    print("PASS: four recorded batches, eleven vial spectra, recipes, logs, metadata, references and checksums.")
    print("This is an archive completeness check, not a reproduction of all manuscript figures.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Parent of the four original experiment folders.")
    parser.add_argument("--output", type=Path, help="New folder whose Dataset/ child can be uploaded.")
    parser.add_argument("--check-root", type=Path, help="Check a repository after the raw files were uploaded.")
    args = parser.parse_args()
    try:
        if args.check_root is not None:
            if args.source is not None or args.output is not None:
                parser.error("Use --check-root by itself.")
            check(args.check_root)
        else:
            if args.source is None or args.output is None:
                parser.error("Provide both --source and --output, or use --check-root.")
            stage(args.source, args.output, Path(__file__).resolve().parents[1])
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"Archive check failed: {exc}\n")


if __name__ == "__main__":
    main()
