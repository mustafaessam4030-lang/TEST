"""Run the server: ``python -m eka`` (settings from EKA_* environment variables)."""
from __future__ import annotations

import logging

import uvicorn

from .api import create_app
from .config import load_settings


def main():
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
