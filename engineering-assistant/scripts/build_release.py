"""Build the distributable zip.

    python scripts/build_release.py --model-cache <fastembed cache dir> [--out dist]
                                    [--wheels win_amd64:3.11,3.12,3.13] [--split-mb 25]

Produces ``dist/EngineeringKnowledgeAssistant-<version>.zip`` with one root
folder holding the app, launchers, an empty ``pdfs/`` folder, the bundled
retrieval model and the docs. With ``--wheels`` it also builds a separate
``...-offline-packages-<platform>.zip`` whose ``wheels/`` folder the setup
scripts install from without internet access.

With ``--split-mb N`` no zip is larger than about N MB, for channels with an
upload limit: the model file is cut into ``.partNN`` pieces shipped in
``...-model-partXofY.zip`` files (setup re-joins them and checks the SHA-256,
see ``eka/assemble.py``), and the offline packages are built per Python
version and spread over several zips. All zips are extracted into the same
folder.

The model folder is copied with symlinks resolved (Hugging Face caches use
them; zip files and Windows do not handle them well) and without the blobs
they point to, so it loads offline as-is.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "EngineeringKnowledgeAssistant"
MODEL_FOLDER = "models--Qdrant--bge-small-en-v1.5-onnx-Q"
COPY = ["eka", "scripts", "samples", "tests", "docs", "README.md", "pyproject.toml",
        "requirements.txt", "requirements-optional.txt", "requirements-dev.txt"]
EVAL = ["questions.json", "heldout.json"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", "data", "dist", "results", ".venv"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def copy_tree(src: Path, dst: Path):
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src)
        if any(part in SKIP_DIRS for part in rel.parts) or path.suffix in SKIP_SUFFIX:
            continue
        if path.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
        else:
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst / rel)


def copy_model(cache: Path, dst: Path):
    src = cache / MODEL_FOLDER
    if not src.is_dir():
        sys.exit("model not found: {0} (expected a fastembed cache containing {1})".format(cache, MODEL_FOLDER))
    shutil.copytree(src, dst / MODEL_FOLDER, symlinks=False,
                    ignore=shutil.ignore_patterns("blobs", ".locks", "*.lock", "*.incomplete"))


def write_zip(folder: Path, zip_path: Path, root_name: str):
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(folder.rglob("*")):
            rel = Path(root_name) / path.relative_to(folder)
            if path.is_dir():
                info = zipfile.ZipInfo(rel.as_posix() + "/")
                info.external_attr = (stat.S_IFDIR | 0o755) << 16
                zf.writestr(info, "")
                continue
            info = zipfile.ZipInfo.from_file(path, rel.as_posix())
            mode = 0o755 if path.suffix == ".sh" else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, path.read_bytes())


def build_app(stage: Path, model_cache: Path | None):
    for item in COPY:
        copy_tree(ROOT / item, stage / item)
    for name in EVAL:
        copy_tree(ROOT / "eval" / name, stage / "eval" / name)
    release = ROOT / "release"
    for path in sorted(release.iterdir()):
        if path.name == "pdfs_README.txt":
            continue
        data = path.read_bytes()
        if path.suffix == ".bat":   # cmd.exe needs CRLF line endings
            data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
        (stage / path.name).write_bytes(data)
    (stage / "pdfs").mkdir()
    shutil.copy2(release / "pdfs_README.txt", stage / "pdfs" / "README.txt")
    shutil.copy2(ROOT / "START_HERE.md", stage / "START_HERE.md")
    (stage / "VERSION").write_text("{0} (built {1})\n".format(version(), time.strftime("%Y-%m-%d")))
    if model_cache:
        copy_model(model_cache, stage / "models")
    else:
        (stage / "models").mkdir()


def build_wheels(stage: Path, platform: str, pythons: list[str]):
    wheels = stage / "wheels"
    wheels.mkdir(parents=True)
    reqs = [str(ROOT / name) for name in ("requirements.txt", "requirements-optional.txt", "requirements-dev.txt")]
    for py in pythons:
        base = [sys.executable, "-m", "pip", "download", "--quiet", "--only-binary=:all:", "--dest", str(wheels),
                "--python-version", py]
        if platform != "native":
            base += ["--platform", platform]
        cmd = base + ["pip"]
        for req in reqs:
            cmd += ["-r", req]
        print("downloading packages for Python", py, platform)
        subprocess.run(cmd, check=True)
        if platform.startswith("win"):
            add_marker_dependencies(wheels, py, base)
    (stage / "README-offline-packages.txt").write_text(
        "Offline packages for the Engineering Knowledge Assistant ({0}; Python {1}).\n\n"
        "Extract this zip into the same place as the app zip, so the 'wheels' folder sits\n"
        "beside setup.bat / setup.sh, then run setup. It installs from this folder and\n"
        "needs no internet access.\n".format(platform, ", ".join(pythons)))


def _wheel_requirements(wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as zf:
        meta = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        text = zf.read(meta).decode("utf-8", "replace")
    return [line.split(":", 1)[1].strip() for line in text.splitlines() if line.startswith("Requires-Dist:")]


def add_marker_dependencies(wheels: Path, py: str, base_cmd: list[str]):
    """pip download evaluates environment markers for the machine it runs on, so
    Windows-only dependencies (colorama, win32_setctime, ...) are missed when
    building on Linux. Re-evaluate every wheel's requirements for Windows and
    fetch what is missing, until nothing new is needed."""
    try:
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.utils import canonicalize_name
    env = {"sys_platform": "win32", "platform_system": "Windows", "os_name": "nt", "platform_machine": "AMD64",
           "python_version": py, "python_full_version": py + ".0", "implementation_name": "cpython",
           "platform_python_implementation": "CPython", "extra": ""}
    while True:
        have = {canonicalize_name(w.name.split("-")[0]) for w in wheels.glob("*.whl")}
        missing = set()
        for wheel in wheels.glob("*.whl"):
            for spec in _wheel_requirements(wheel):
                req = Requirement(spec)
                if (req.marker is None or req.marker.evaluate(env)) and canonicalize_name(req.name) not in have:
                    missing.add(str(Requirement(spec.split(";")[0])))
        if not missing:
            return
        print("  Windows-only dependencies:", ", ".join(sorted(missing)))
        subprocess.run(base_cmd + sorted(missing), check=True)


def split_large_files(stage: Path, folder: Path, limit: int, tmp: Path) -> list[Path]:
    """Cut files over ``limit`` bytes into parts; returns one staging folder per part."""
    part_stages: list[Path] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.stat().st_size > limit):
        data = path.read_bytes()
        chunks = [data[i:i + limit] for i in range(0, len(data), limit)]
        names = ["{0}.part{1:02d}".format(path.name, i + 1) for i in range(len(chunks))]
        for i, (chunk, name) in enumerate(zip(chunks, names)):
            if i >= len(part_stages):
                part_stages.append(tmp / "part{0}".format(i + 1) / NAME)
            dst = part_stages[i] / path.relative_to(stage).parent / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(chunk)
        manifest = {"name": path.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "parts": names}
        path.with_name(path.name + ".parts.json").write_text(json.dumps(manifest, indent=1))
        path.unlink()
    return part_stages


def group_files(files: list[Path], limit: int) -> list[list[Path]]:
    """First-fit-decreasing: spread files over as few groups of at most ``limit`` bytes as possible."""
    groups: list[tuple[int, list[Path]]] = []
    for f in sorted(files, key=lambda f: f.stat().st_size, reverse=True):
        size = f.stat().st_size
        for i, (used, members) in enumerate(groups):
            if used + size <= limit:
                groups[i] = (used + size, members + [f])
                break
        else:
            groups.append((size, [f]))
    return [members for _, members in groups]


def report(path: Path):
    print("{0}  {1:.1f} MB".format(path, path.stat().st_size / 1e6))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-cache", type=Path, help="fastembed cache folder that contains " + MODEL_FOLDER)
    ap.add_argument("--no-model", action="store_true", help="build without the semantic model (keyword search only)")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    ap.add_argument("--wheels", action="append", default=[],
                    help="PLATFORM:PY[,PY...] e.g. win_amd64:3.11,3.12,3.13 (repeatable; 'native' = this machine)")
    ap.add_argument("--split-mb", type=float, default=0, help="keep every zip under about this many MB")
    args = ap.parse_args()
    if not args.no_model and not args.model_cache:
        ap.error("--model-cache is required (or pass --no-model)")
    ver = version()
    with tempfile.TemporaryDirectory(prefix="eka-release-") as tmp:
        stage = Path(tmp) / NAME
        stage.mkdir()
        build_app(stage, None if args.no_model else args.model_cache)
        limit = int(args.split_mb * 1e6 * 0.97) if args.split_mb else 0   # leave room for zip headers
        if limit:
            parts = split_large_files(stage, stage / "models", limit, Path(tmp) / "model-parts")
            for i, part in enumerate(parts):
                pzip = args.out / "{0}-{1}-model-part{2}of{3}.zip".format(NAME, ver, i + 1, len(parts))
                write_zip(part, pzip, NAME)
                report(pzip)
        app_zip = args.out / "{0}-{1}.zip".format(NAME, ver)
        write_zip(stage, app_zip, NAME)
        report(app_zip)
        for spec in args.wheels:
            platform, _, pys = spec.partition(":")
            pythons = [p for p in pys.split(",") if p] or ["{0}.{1}".format(*sys.version_info[:2])]
            if not limit:
                wstage = Path(tmp) / ("wheels-" + platform)
                build_wheels(wstage, platform, pythons)
                wzip = args.out / "{0}-{1}-offline-packages-{2}.zip".format(NAME, ver, platform)
                write_zip(wstage, wzip, NAME)
                report(wzip)
                continue
            for py in pythons:      # one set per Python version, spread over zips under the limit
                wstage = Path(tmp) / "wheels-{0}-{1}".format(platform, py)
                build_wheels(wstage, platform, [py])
                groups = group_files(sorted((wstage / "wheels").iterdir()), limit)
                for i, group in enumerate(groups):
                    gstage = Path(tmp) / "wheels-{0}-{1}-{2}".format(platform, py, i + 1)
                    (gstage / "wheels").mkdir(parents=True)
                    for f in group:
                        shutil.copy2(f, gstage / "wheels" / f.name)
                    if i == 0:
                        shutil.copy2(wstage / "README-offline-packages.txt", gstage / "README-offline-packages.txt")
                    wzip = args.out / "{0}-{1}-offline-packages-{2}-py{3}-part{4}of{5}.zip".format(
                        NAME, ver, platform, py, i + 1, len(groups))
                    write_zip(gstage, wzip, NAME)
                    report(wzip)

if __name__ == "__main__":
    os.umask(0o022)
    main()
