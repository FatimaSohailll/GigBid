"""Generate the complete DevSim dataset. The same seed always produces identical files."""

import hashlib
import json
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from devsim.datagen.catalog import load_catalog
from devsim.datagen.corpus import write_json, write_persona, write_text
from devsim.datagen.jobs import build_clients, build_dev_set, build_history, build_stream

DEFAULT_SEED = 20261101
SIMULATION_START = datetime(2026, 11, 1, tzinfo=UTC)
STARTING_CONNECTS = 500


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    write_text(path, "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def _sha256_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "MANIFEST.json"
    }


def generate(
    out: Path,
    seed: int = DEFAULT_SEED,
    history: int = 1500,
    per_persona: int = 200,
    stream: int = 150,
) -> dict[str, Any]:
    personas = load_catalog()
    pools = build_clients(seed)
    jobs: list[dict[str, Any]] = []

    for persona in personas:
        write_persona(out / "personas", persona, seed)
        others = tuple(other for other in personas if other is not persona)
        dev = build_dev_set(persona, others, per_persona, pools, seed)
        jobs += [item.job for item in dev]
        _write_jsonl(out / "dev_set" / f"{persona.persona_id}.jsonl", [i.gold for i in dev])

    live = build_stream(personas, stream, pools, seed, SIMULATION_START)
    jobs += [item.job for item in live]
    _write_jsonl(out / "stream_gold.jsonl", [item.gold for item in live])

    market = out / "marketplace"
    write_json(market / "jobs.json", jobs)
    write_json(market / "clients.json", [client for pool in pools.values() for client in pool])
    write_json(market / "history.json", build_history(personas, history, seed))
    write_json(
        market / "freelancers.json",
        [
            {
                "freelancer_id": persona.persona_id,
                "display_name": persona.display_name,
                "starting_connects": STARTING_CONNECTS,
            }
            for persona in personas
        ],
    )
    write_text(
        out / "policy_pack" / "marketplace_policy.md",
        resources.files("devsim.datagen").joinpath("policy_pack.md").read_text("utf-8"),
    )

    manifest = {
        "generator": "gigbid-devsim",
        "official_gigsim": False,
        "seed": seed,
        "counts": {
            "history": history,
            "dev_per_persona": per_persona,
            "stream": stream,
            "personas": len(personas),
        },
        "files": _sha256_tree(out),
    }
    write_json(out / "MANIFEST.json", manifest)
    return manifest
