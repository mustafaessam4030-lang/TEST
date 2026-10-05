"""
    python -m controlplane serve
    python -m controlplane create-admin --email you@mantrac.com --name "Your Name"
    python -m controlplane add-worker --name "ATA-WORKER-01"
    python -m controlplane workers
    python -m controlplane users

Settings come from the environment (see PLATFORM.md, Environment variables).
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m controlplane")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="run the web app and API")
    admin = sub.add_parser("create-admin", help="create an administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    admin.add_argument("--entra", action="store_true",
                       help="this admin signs in with Microsoft (no local password)")
    admin.add_argument("--password-stdin", action="store_true",
                       help="read the password from standard input")
    worker = sub.add_parser("add-worker", help="issue a credential for a Windows worker")
    worker.add_argument("--name", required=True)
    sub.add_parser("workers", help="list workers and their heartbeat")
    sub.add_parser("users", help="list accounts")
    args = parser.parse_args(argv)

    # The control plane's learning store holds real operational data.
    os.environ.setdefault("ATLAS_DATA_ORIGIN", "production")
    from controlplane.config import Settings

    if args.cmd == "serve":
        from dashboard import server as tower_server
        from controlplane.app import serve
        settings = Settings()
        if settings.intel_dir:
            os.environ["ATLAS_INTEL_DIR"] = settings.intel_dir
        tower_server.LEARNING["on"] = True
        serve(settings)
        return 0

    from controlplane.app import App
    app = App(Settings())
    if args.cmd == "create-admin":
        password = None
        if not args.entra:
            if args.password_stdin:
                password = sys.stdin.readline().rstrip("\n")
            else:
                password = getpass.getpass("Password for {0}: ".format(args.email))
                if getpass.getpass("Again: ") != password:
                    print("The passwords differ.")
                    return 1
        from controlplane.users import AccessError
        try:
            user, _ = app.users.create(args.email, args.name, "ADMIN", password=password,
                                       auth_source="entra" if args.entra else "local")
        except AccessError as error:
            print(error)
            return 1
        print("Admin {0} created ({1}).".format(user["work_email"], user["user_id"]))
        return 0
    if args.cmd == "add-worker":
        worker_id, token = app.orch.add_worker(args.name)
        print("Worker {0} registered.".format(worker_id))
        print("Give the worker this token (ATA_WORKER_TOKEN). It is shown only now:")
        print(token)
        return 0
    if args.cmd == "workers":
        for w in app.orch.workers():
            print("{0}  {1:<20} {2:<9} heartbeat {3}s ago  run {4}".format(
                w["worker_id"], w["name"] or "", w["state"],
                w["heartbeat_age_s"], w["current_run_id"] or "-"))
        return 0
    if args.cmd == "users":
        for u in app.users.list():
            print("{0:<34} {1:<9} {2}".format(u["work_email"], u["role"],
                                              "active" if u["active"] else "inactive"))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
