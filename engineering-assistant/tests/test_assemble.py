"""Re-joining a file delivered in parts (eka.assemble), as setup does for the model."""
import hashlib
import json

from eka.assemble import assemble


def split(folder, name, data, size):
    parts = []
    for i in range(0, len(data), size):
        part = "{0}.part{1:02d}".format(name, i // size + 1)
        (folder / part).write_bytes(data[i:i + size])
        parts.append(part)
    (folder / (name + ".parts.json")).write_text(json.dumps(
        {"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "parts": parts}))
    return parts


def test_parts_are_joined_checked_and_removed(tmp_path):
    data = bytes(range(256)) * 1000
    sub = tmp_path / "m" / "snap"
    sub.mkdir(parents=True)
    split(sub, "model.onnx", data, 100_000)
    assert assemble(tmp_path) == []
    assert (sub / "model.onnx").read_bytes() == data
    assert sorted(p.name for p in sub.iterdir()) == ["model.onnx"]
    assert assemble(tmp_path) == []                       # running setup again is harmless


def test_missing_or_damaged_parts_are_reported(tmp_path):
    data = b"x" * 250_000
    parts = split(tmp_path, "model.onnx", data, 100_000)
    (tmp_path / parts[1]).unlink()
    problems = assemble(tmp_path)
    assert len(problems) == 1 and "missing model.onnx.part02" in problems[0]
    assert not (tmp_path / "model.onnx").exists()
    (tmp_path / parts[1]).write_bytes(b"y" * 100_000)    # wrong content
    problems = assemble(tmp_path)
    assert len(problems) == 1 and "damaged" in problems[0]
    assert not (tmp_path / "model.onnx").exists() and (tmp_path / "model.onnx.parts.json").exists()
