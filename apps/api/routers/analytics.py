"""Reply and hire rates, Connects spent, run cost and rejection reasons."""

from fastapi import APIRouter

from apps.api.schemas.api import Analytics, CategoryStats, RejectionStat
from apps.api.services import CurrentUser, Services

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("", response_model=Analytics)
async def analytics(user: CurrentUser, services: Services) -> Analytics:
    repository = services.repository
    categories = await repository.analytics_by_category(user["id"])
    runs = await repository.analytics_runs(user["id"])
    return Analytics(
        by_category=[
            CategoryStats(
                **row,
                reply_rate=round(row["replied"] / row["submitted"], 4),
                hire_rate=round(row["hired"] / row["submitted"], 4),
            )
            for row in categories
        ],
        runs=runs["runs"],
        mean_tokens_per_run=round(runs["mean_tokens"], 1),
        p95_latency_seconds=round(runs["p95_seconds"], 2),
        top_rejection_reasons=[
            RejectionStat(**row) for row in await repository.top_rejections(user["id"])
        ],
    )
