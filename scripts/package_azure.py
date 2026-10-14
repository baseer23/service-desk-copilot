"""Build a reviewed source deployment ZIP without local stores or credentials."""

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def revision_suffix(specification: dict) -> str:
    """A new image, bootstrap or configuration must create a distinct revision."""
    canonical = json.dumps(specification, sort_keys=True, separators=(",", ":"))
    return "reviewed-" + hashlib.sha256(canonical.encode()).hexdigest()[:14]


def package(root: Path, destination: Path, wheels: Path | None = None):
    files = []
    for file in (root / "backend").rglob("*"):
        if (
            file.is_file()
            and (
                file.suffix == ".py"
                or file.relative_to(root)
                in {
                    Path("backend/app/repositories/fixture_data.json"),
                    Path("backend/app/voice/prompts/support_v1.md"),
                }
            )
            and not {"__pycache__", "tests", "testing"}.intersection(file.parts)
        ):
            files.append((file, file.relative_to(root)))
    for file in (root / "frontend" / "dist").rglob("*"):
        if file.is_file():
            files.append((file, file.relative_to(root)))
    files.append((root / "requirements-azure.txt", Path("requirements.txt")))
    if not (root / "frontend" / "dist" / "index.html").is_file():
        raise ValueError("Build the frontend before packaging")
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        for source, target in sorted(files):
            archive.write(source, str(target))
        if wheels is not None:
            archives = sorted(wheels.glob("*.whl"))
            if not archives:
                raise ValueError("No reviewed Linux wheels found")
            libraries = Path(".python_packages/lib/site-packages")
            for wheel in archives:
                if "macosx" in wheel.name or "win_" in wheel.name:
                    raise ValueError("Hosted bundles require Linux or platform-independent wheels")
                with ZipFile(wheel) as dependency:
                    for item in dependency.infolist():
                        if item.is_dir():
                            continue
                        parts = Path(item.filename).parts
                        if Path(item.filename).is_absolute() or ".." in parts:
                            raise ValueError("Invalid wheel member")
                        if parts[0].endswith(".data"):
                            if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                                continue
                            parts = parts[2:]
                        archive.writestr(str(libraries.joinpath(*parts)), dependency.read(item))
    destination.chmod(0o600)
    return {
        "files": len(files),
        "bytes": destination.stat().st_size,
        "local_stores_included": False,
        "environment_files_included": False,
        "linux_dependencies_included": wheels is not None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--linux-wheels", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(Path(__file__).resolve().parents[1], args.output, args.linux_wheels)))


if __name__ == "__main__":
    main()
