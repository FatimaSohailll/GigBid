"""Client conversations with suggested replies."""

from fastapi import APIRouter, HTTPException

from apps.api.schemas.api import ConversationDetail, ConversationSummary
from apps.api.services import CurrentUser, Services, conversation_thread, read_thread

router = APIRouter(prefix="/api/conversations", tags=["messages"])


@router.get("", response_model=list[ConversationSummary])
async def conversations(user: CurrentUser, services: Services) -> list[ConversationSummary]:
    runs = await services.repository.runs(user["id"])
    return [
        ConversationSummary(
            conversation_id=run["ref_id"], thread_id=run["thread_id"], status=run["status"]
        )
        for run in runs
        if run["kind"] == "conversation"
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def detail(
    conversation_id: str,
    user: CurrentUser,
    services: Services,
) -> ConversationDetail:
    thread_id = conversation_thread(user["id"], conversation_id)
    run = await services.repository.run(user["id"], thread_id)
    if run is None:
        raise HTTPException(404, "Conversation not found.")
    values, pending = await read_thread(services, "conversation", thread_id)
    replies = values.get("reply_drafts", [])
    intent = values.get("intent")
    return ConversationDetail(
        conversation_id=conversation_id,
        thread_id=thread_id,
        status=run["status"],
        messages=values.get("client_messages", []),
        intent=intent.model_dump(mode="json") if intent else None,
        reply=replies[-1] if replies else None,
        proposed_slots=values.get("proposed_slots", []),
        pending_approval=pending,
    )
