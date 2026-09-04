"""
Real numbers from ATLAS, for the dashboard.

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
    from ml import episodes as ml_episodes
    from ml import identity as ml_identity
    from ml import model as ml_model
    from ml import predictor as ml_predictor
    from ml import telemetry as ml_telemetry
    from ml import trainer as ml_trainer
    AVAILABLE = True
    IMPORT_ERROR = None
except Exception as error:      # pragma: no cover - environment dependent
    AVAILABLE = False
    IMPORT_ERROR = str(error)
    ml_config = ml_episodes = ml_identity = ml_model = None
    ml_predictor = ml_telemetry = ml_trainer = None

# Shown when the package cannot be imported at all — the panel still needs a
# name for the thing that is missing.
ENGINE_NAME = "ATLAS"
ENGINE_FULL_NAME = "Adaptive Logistics Strategy Engine"


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
                    # Whatever is in the file, this becomes a UI label. A
                    # carrier code is a short token; anything else is a stray
                    # row and is bucketed as unknown rather than rendered.
                    provider = ((event.get("context") or {}).get("provider")
                                or "unknown")
                    provider = str(provider).strip()
                    if not provider or len(provider) > 12 or not provider.isalnum():
                        provider = "unknown"
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

    `status` is one of ENABLED, SHADOW, FALLBACK, DISABLED or UNAVAILABLE —
    the same words the run log prints at startup, so the dashboard and the log
    can never tell different stories. SHADOW is separate from ENABLED on
    purpose: a model that is loaded and learning but changing nothing must not
    be shown as one that is steering the automation.
    """
    if not AVAILABLE:
        return {
            "available": False, "status": "UNAVAILABLE", "online": False,
            "engine": ENGINE_NAME, "engine_full_name": ENGINE_FULL_NAME,
            "error": IMPORT_ERROR, "model": None, "telemetry": _telemetry_summary(),
            "config": {},
        }

    try:
        state = ml_predictor.status()
    except Exception as error:
        return {
            "available": True, "status": "FALLBACK", "online": False,
            "engine": ENGINE_NAME, "engine_full_name": ENGINE_FULL_NAME,
            "error": str(error), "model": None,
            "telemetry": _telemetry_summary(), "config": {},
        }

    enabled = bool(state.get("enabled"))
    loaded = bool(state.get("model_loaded"))
    mode = state.get("mode") or "shadow"
    # SHADOW is its own status. A loaded model in shadow mode is running and
    # learning, and it is also changing nothing — calling that "ENABLED" would
    # tell an operator the automation is being steered when it is not.
    if not enabled:
        status = "DISABLED"
    elif not loaded:
        status = "FALLBACK"
    elif mode == "shadow":
        status = "SHADOW"
    else:
        status = "ENABLED"

    summary = state.get("summary") or {}
    meta = summary.get("meta") or {}
    model = None
    if loaded:
        model = {
            "path": state.get("loaded_from"),
            "name": ENGINE_NAME,
            # `ATLAS/4.2` — file schema and feature space. One string an
            # operator can quote when asking why a model was refused.
            "identifier": summary.get("identifier") or ml_identity.identifier(
                ml_model.MODEL_VERSION, meta.get("feature_version", "?")),
            "version": meta.get("version"),
            "built_at": meta.get("built_at"),
            "contexts": summary.get("cells"),
            "observations": summary.get("observations"),
            "timing_contexts": summary.get("timing_cells"),
            "rows": meta.get("rows"),
            "feature_version": meta.get("feature_version"),
            "label_rule": meta.get("label_rule"),
            "promoted_at": meta.get("promoted_at"),
            "promotion_verdict": meta.get("promotion_verdict"),
            "quarantined": summary.get("quarantined"),
        }

    telemetry = _telemetry_summary()

    trainable = None
    reason = None
    try:
        # The honest count is LABELLED rows, not raw interactions. A file with
        # 4,000 interactions and no read-back verification has nothing to train
        # on, and showing 4,000 would say the opposite.
        rows, report = ml_episodes.join()
        ok, why = ml_trainer.enough_to_train(rows)
        trainable = bool(ok)
        reason = why if ok else ml_episodes.explain_shortfall(report)
        telemetry["usable_rows"] = report.get("kept", 0)
        telemetry["rejected_test_rows"] = report.get("dropped_not_real", 0)
        telemetry["episodes"] = report.get("episodes", 0)
        telemetry["episodes_with_verdict"] = report.get("episodes_with_verdict", 0)
        telemetry["unverified_dropped"] = report.get("dropped_unverified_episode", 0)
        telemetry["label_rule"] = report.get("label_rule")
    except Exception:
        pass

    return {
        "available": True,
        "status": status,
        "online": status == "ENABLED",
        "engine": ml_identity.NAME,
        "engine_full_name": ml_identity.FULL_NAME,
        "engine_role": ml_identity.ROLE,
        "mode": mode,
        # Never overstate this. True only when a promotion recorded a BETTER
        # verdict against real held-out telemetry, and it was not forced.
        "proven": bool(state.get("proven")),
        "error": state.get("error"),
        "model": model,
        "telemetry": telemetry,
        "trainable": trainable,
        "trainable_reason": reason,
        "config": {
            "enabled": enabled,
            "mode": mode,
            "confidence_threshold": ml_config.ML_CONFIDENCE_THRESHOLD,
            "model_path": str(ml_config.active_model_path()),
            "champion_path": str(ml_config.CHAMPION_PATH),
            "challenger_path": str(ml_config.CHALLENGER_PATH),
            "min_support": ml_config.MIN_SUPPORT,
            "min_support_per_arm": ml_config.MIN_SUPPORT_PER_ARM,
            "require_verified_label": ml_config.REQUIRE_VERIFIED_LABEL,
            "half_life_days": ml_config.HALF_LIFE_DAYS,
            "exploration": ml_config.ML_EXPLORATION_ENABLED,
            "min_observations": ml_config.ML_MIN_OBSERVATIONS,
            "max_wait_ms": ml_config.ML_MAX_WAIT,
            "telemetry_enabled": ml_config.TELEMETRY_ENABLED,
        },
    }
