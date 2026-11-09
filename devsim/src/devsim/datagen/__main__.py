"""Generate DevSim data: ``python -m devsim.datagen --out data/processed/devsim``."""

import argparse
from pathlib import Path

from devsim.datagen.generate import DEFAULT_SEED, generate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("data/processed/devsim"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--history", type=int, default=1500)
    parser.add_argument("--per-persona", type=int, default=200)
    parser.add_argument("--stream", type=int, default=150)
    arguments = parser.parse_args()
    manifest = generate(
        arguments.out,
        arguments.seed,
        arguments.history,
        arguments.per_persona,
        arguments.stream,
    )
    print(f"Wrote {len(manifest['files'])} files to {arguments.out} (seed {arguments.seed})")


if __name__ == "__main__":
    main()
