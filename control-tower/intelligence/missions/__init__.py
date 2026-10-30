"""
ATLAS Missions — Deep Mission Mode and Emergency Mode (ATLAS_MISSIONS_PLAN.md).

An orchestration layer over what ATLAS already has. It never writes: a
mission reads records, checks its own numbers, and proposes next steps that
go through the existing approvals and Human Action queue.

    classify   is this a mission? the proposal shown before anything runs
    engine     Mission Commander + Workload Manager (persistent, resumable)
    specialists one function per specialist role
    models     model registry, router, invocation ledger
    registry   specialists, websites, templates
    db         the SQLite store (missions.db in the intelligence folder)
"""
