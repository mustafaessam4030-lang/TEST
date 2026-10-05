"""
ATA Control Tower — control plane.

The multi-user web application: sign-in, roles, the authoritative run record,
the worker protocol, the Human Action remote session relay and the audit
trail. It never drives a browser and never touches the Hub; the Windows
worker does that, by running the existing automation (update_eta.py) exactly
as the supervisor always has.

    python -m controlplane serve            the web app + API
    python -m controlplane create-admin     first administrator
    python -m controlplane add-worker       issue a worker credential

Standard library only, except PostgreSQL, which needs psycopg when
DATABASE_URL points at one. See PLATFORM.md.
"""
