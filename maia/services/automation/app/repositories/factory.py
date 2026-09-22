"""One place that decides which store is in use. The service, the scripts and
the doctor all call this, so they can never disagree about where data goes."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.config import ROOT


def build_local_store(settings: Any) -> Any:
    """The local JSON store: JSON + TXT + screenshots under logs/sis-results/."""
    from app.repositories.local_json_repo import LocalJsonRepository

    store_dir = Path(settings.local_store_dir)
    if not store_dir.is_absolute():
        store_dir = ROOT / store_dir
    # The live credential values, held only so they can be searched for and
    # masked in anything written. They are never logged or returned.
    secrets = tuple(v for v in (os.environ.get("SIS_USERNAME"),
                                os.environ.get("SIS_PASSWORD")) if v)
    return LocalJsonRepository(store_dir, secrets=secrets)


def build_snowflake(settings: Any) -> Any:
    from app.repositories import snowflake_config
    from app.repositories.snowflake_repo import SnowflakeEquipmentRepository

    cfg = snowflake_config.load(settings)
    problems = cfg.problems()
    if problems:
        # Refuse to start half-configured: a service that "runs" but cannot
        # save fails every lookup at the last step instead of at startup.
        raise RuntimeError("Snowflake is selected but not configured: " + "; ".join(problems))
    return SnowflakeEquipmentRepository(cfg.connect_kwargs(), secrets=cfg.secrets())


def build_repository(settings: Any) -> Any:
    if settings.repository == "snowflake":
        warehouse = build_snowflake(settings)
        if not settings.local_artifacts:
            return warehouse
        from app.repositories.mirrored_repo import MirroredRepository

        return MirroredRepository(primary=warehouse, mirror=build_local_store(settings))
    if settings.repository == "memory":
        from app.repositories.memory_repo import MemoryEquipmentRepository

        return MemoryEquipmentRepository()
    # Default: the local JSON store. Every successful lookup lands in
    # logs/sis-results/, and a failed write fails the lookup rather than being
    # reported as a success.
    return build_local_store(settings)
