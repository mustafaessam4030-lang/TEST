"""Run the server: ``python -m eka``.

Settings come from EKA_* environment variables and, if present, ``config.env``
in the current folder. The process id is written to ``<data>/server.pid`` so
stop scripts on any OS can find it, and a log to ``<data>/eka.log``.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import threading
import webbrowser

import uvicorn

from .api import create_app
from .config import load_config_file, load_settings


def main():
    config_file = load_config_file()
    settings = load_settings()
    settings.ensure_dirs()
    fmt = "%(asctime)s %(levelname)s %(name)s: %(message)s"
    logging.basicConfig(level=settings.log_level.upper(), format=fmt)
    # also keep a small rotating log in the data folder for troubleshooting (no document text is logged)
    file_log = logging.handlers.RotatingFileHandler(settings.data_dir / "eka.log", maxBytes=2_000_000,
                                                    backupCount=2, encoding="utf-8")
    file_log.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(file_log)
    log = logging.getLogger("eka")
    url = "http://{0}:{1}".format("127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host, settings.port)
    log.info("config file: %s", config_file or "none (defaults and environment)")
    log.info("data folder: %s", settings.data_dir)
    log.info("PDF folder:  %s", settings.inbox_path or "disabled")
    log.info("open %s in a browser", url)
    pid_file = settings.data_dir / "server.pid"
    pid_file.write_text(str(os.getpid()))
    if settings.open_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    try:
        # no per-request access log: page and PDF links can carry the team key as ?token=
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port,
                    log_level=settings.log_level.lower(), access_log=False)
    finally:
        try:
            if pid_file.read_text().strip() == str(os.getpid()):
                pid_file.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
