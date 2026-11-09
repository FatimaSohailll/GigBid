"""Load and validate seed data."""

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from devsim.models import ClientRecord, Freelancer, Job


@dataclass(frozen=True)
class SeedData:
    jobs: list[Job]
    clients: list[ClientRecord]
    freelancers: list[Freelancer]


def _assert_unique(kind: str, ids: Iterable[str]) -> None:
    duplicates = [item for item, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"Duplicate {kind} ids in seed data: {sorted(duplicates)}")


def validate_seed(seed: SeedData) -> None:
    """Reject seed data with duplicate ids or jobs that reference unknown clients."""
    _assert_unique("job", (job.job_id for job in seed.jobs))
    _assert_unique("client", (client.client_id for client in seed.clients))
    _assert_unique("freelancer", (person.freelancer_id for person in seed.freelancers))
    client_ids = {client.client_id for client in seed.clients}
    orphans = sorted(job.job_id for job in seed.jobs if job.client_id not in client_ids)
    if orphans:
        raise ValueError(f"Jobs reference unknown clients: {orphans}")


def load_seed(seed_dir: Path | None = None) -> SeedData:
    """Load seed JSON from a directory, or the packaged core seed when none is given."""
    root: Traversable | Path = (
        seed_dir if seed_dir is not None else resources.files("devsim").joinpath("seed/core")
    )

    def read(name: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = json.loads(root.joinpath(name).read_text(encoding="utf-8"))
        return items

    seed = SeedData(
        jobs=[Job.model_validate(item) for item in read("jobs.json")],
        clients=[ClientRecord.model_validate(item) for item in read("clients.json")],
        freelancers=[Freelancer.model_validate(item) for item in read("freelancers.json")],
    )
    validate_seed(seed)
    return seed
