"""
Real numbers from the learning layer, for the dashboard.

Every value here is read from the live ml package — the loaded model, the
telemetry file on disk, the configuration actually in force. Nothing is
invented, and when something is unknown the field is null rather than zero, so
the UI can say "unknown" instead of implying a measurement that was never
taken.

Read-only. This module never trains, never writes a model, never changes a
setting.
"""

import json
from pathlib import Path

try:
    from ml import config as ml_config
    from ml import dataset as ml_dataset
    from ml import predictor as ml_predictor
    from ml import telemetry as ml_telemetry
    AVAILABLE = True
    IMPORT_ERROR = None
except Exception as error:      # pragma: no cover - environment dependent
    AVAILABLE = False
    IMPORT_ERROR = str(error)
    ml_config = ml_dataset = ml_predictor = ml_telemetry = None


def _telemetry_summary(limit_bytes=6 * 1024 * 1024):
    """
    Counts from the telemetry file.

    Reads the tail only on a large file: the dashboard wants a shape, not a
    census, and blocking the UI thread on a 30MB scan would be a poor trade.
    """
    summary = {
        "path": None, "rows": 0, "interactions": 0, "decisions": 0,
        "used": 0, "declined": 0, "by_carrier": {}, "truncated": False,
    }
    if not AVAILABLE:
        return summary
    path = Path(ml_config.TELEMETRY_PATH)
    summary["path"] = str(path)
    if not path.exists():
        return summary
    try:
        size = path.stat().st_size
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if size > limit_bytes:
                handle.seek(size - limit_bytes)
                handle.readline()          # drop the partial first line
                summary["truncated"] = True
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                summary["rows"] += 1
                kind = event.get("kind")
                if kind == "decision":
                    summary["decisions"] += 1
                    if event.get("used"):
                        summary["used"] += 1
                    else:
                        summary["declined"] += 1
                elif kind == "interaction":
                    summary["interactions"] += 1
                    provider = ((event.get("context") or {}).get("provider")
                                or "unknown")
                    bucket = summary["by_carrier"].setdefault(
                        provider, {"attempts": 0, "successes": 0, "ms": []})
                    bucket["attempts"] += 1
                    if event.get("success"):
                        bucket["successes"] += 1
                        duration = event.get("duration_ms")
                        if isinstance(duration, (int, float)):
                            bucket["ms"].append(float(duration))
    except OSError:
        pass

    for bucket in summary["by_carrier"].values():
        samples = sorted(bucket.pop("ms"))
        bucket["median_ms"] = (
            round(samples[len(samples) // 2], 1) if samples else None)
        bucket["samples"] = len(samples)
    return summary


def snapshot():
    """
    The whole ML picture for the UI. Never raises.

    `status` is one of ENABLED, FALLBACK, DISABLED or UNAVAILABLE — the same
    words the run log prints at startup, so the dashboard and the log can never
    tell different stories.
    """
    if not AVAILABLE:
        return {
            "available": False, "status": "UNAVAILABLE", "online": False,
            "error": IMPORT_ERROR, "model": None, "telemetry": _telemetry_summary(),
            "config": {},
        }

    try:
        state = ml_predictor.status()
    except Exception as error:
        return {
            "available": True, "status": "FALLBACK", "online": False,
            "error": str(error), "model": None,
            "telemetry": _telemetry_summary(), "config": {},
        }

    enabled = bool(state.get("enabled"))
    loaded = bool(state.get("model_loaded"))
    status = "ENABLED" if (enabled and loaded) else (
        "DISABLED" if not enabled else "FALLBACK")

    summary = state.get("summary") or {}
    meta = summary.get("meta") or {}
    model = None
    if loaded:
        model = {
            "path": state.get("loaded_from"),
            "version": meta.get("version"),
            "built_at": meta.get("built_at"),
            "contexts": summary.get("cells"),
            "observations": summary.get("observations"),
            "timing_contexts": summary.get("timing_cells"),
            "rows": meta.get("rows"),
        }

    telemetry = _telemetry_summary()

    trainable = None
    reason = None
    try:
        rows, report = ml_dataset.load()
        ok, why = ml_dataset.enough_to_train(rows)
        trainable = bool(ok)
        reason = why
        telemetry["usable_rows"] = report.get("kept", 0)
        telemetry["rejected_test_rows"] = report.get("not_real", 0)
    except Exception:
        pass

    return {
        "available": True,
        "status": status,
        "online": status == "ENABLED",
        "error": state.get("error"),
        "model": model,
        "telemetry": telemetry,
        "trainable": trainable,
        "trainable_reason": reason,
        "config": {
            "enabled": enabled,
            "confidence_threshold": ml_config.ML_CONFIDENCE_THRESHOLD,
            "model_path": str(ml_config.ML_MODEL_PATH),
            "exploration": ml_config.ML_EXPLORATION_ENABLED,
            "min_observations": ml_config.ML_MIN_OBSERVATIONS,
            "max_wait_ms": ml_config.ML_MAX_WAIT,
            "telemetry_enabled": ml_config.TELEMETRY_ENABLED,
        },
    }
