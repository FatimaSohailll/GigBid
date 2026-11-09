"""Run DevSim: ``python -m devsim``. State is in memory, so it must stay a single process."""

import os

import uvicorn

from devsim.app import create_app
from devsim.config import load_settings


def main() -> None:
    uvicorn.run(
        create_app(load_settings()),
        host=os.environ.get("DEVSIM_HOST", "127.0.0.1"),
        port=int(os.environ.get("DEVSIM_PORT", "8081")),
        workers=1,
    )


if __name__ == "__main__":
    main()
