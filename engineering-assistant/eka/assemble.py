"""Re-join files that were delivered in parts: ``python -m eka.assemble [folder]``.

Large files (the search model) can be shipped as several smaller zips, each
holding ``<name>.partNN`` pieces and a ``<name>.parts.json`` manifest. Once
all the zips are extracted into the same folder, this joins the pieces,
checks the SHA-256 against the manifest and removes the pieces. It uses
only the standard library, so setup can run it before anything is installed.
Exit code 1 means a piece is missing or the result does not match.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def assemble(root: Path) -> list[str]:
    """Join every manifest under ``root``. Returns problems (empty when all is well)."""
    problems = []
    for manifest in sorted(root.rglob("*.parts.json")):
        info = json.loads(manifest.read_text(encoding="utf-8"))
        target = manifest.parent / info["name"]
        parts = [manifest.parent / p for p in info["parts"]]
        if target.is_file() and _sha256(target) == info["sha256"]:
            _cleanup(manifest, parts)
            continue
        missing = [p.name for p in parts if not p.is_file()]
        if missing:
            problems.append("{0}: missing {1} (extract every model-part zip into this folder)".format(
                info["name"], ", ".join(missing)))
            continue
        tmp = target.with_name(target.name + ".joining")
        digest = hashlib.sha256()
        with open(tmp, "wb") as out:
            for part in parts:
                data = part.read_bytes()
                digest.update(data)
                out.write(data)
        if digest.hexdigest() != info["sha256"]:
            tmp.unlink()
            problems.append("{0}: the joined file is damaged (download the model-part zips again)".format(info["name"]))
            continue
        tmp.replace(target)
        _cleanup(manifest, parts)
        print("assembled", target)
    return problems


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _cleanup(manifest: Path, parts: list[Path]):
    for path in [*parts, manifest]:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "models")
    if not root.is_dir():
        return 0
    problems = assemble(root)
    for problem in problems:
        print("PROBLEM:", problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
