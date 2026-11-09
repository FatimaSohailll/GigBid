"""Client Liaison: read client replies, draft a response or slots, and wait for approval."""

import difflib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from langgraph.types import interrupt
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.guardrails import delimit_untrusted, scan_job_post, scan_outgoing_text
from agent.handoffs import (
    ClientIntent,
    ComplianceAction,
    ComplianceVerdict,
    IntentClassification,
    ReplyDraft,
    RiskFlag,
)
from agent.nodes.compliance import model_review, rule_violations
from agent.runtime import AgentRuntime
from agent.state import (
    ApprovalAction,
    ApprovalRecord,
    ConversationState,
    OutcomeRecord,
    ThreadStatus,
)

_MAX_HISTORY_MESSAGES = 12


class _Reply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1)


class ReplyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "edit", "reject"]
    edited_body: str | None = Field(default=None, min_length=1)
    reason: str | None = None
    selected_slot: datetime | None = None

    @model_validator(mode="after")
    def _complete(self) -> "ReplyDecision":
        if self.action == "edit" and self.edited_body is None:
            raise ValueError("an edit must include the edited text")
        if self.action == "reject" and not self.reason:
            raise ValueError("a rejection must include a reason")
        return self


def _key(state: ConversationState, step: str) -> str:
    """Stable for one reply to one message, and different after each human edit."""
    edits = sum(1 for item in state.get("approvals", []) if item.action is ApprovalAction.EDIT)
    message = state["latest_message"]["message_id"]
    raw = f"{state['user_id']}.{state['conversation_id']}.{message}.e{edits}.{step}"
    return re.sub(r"[^A-Za-z0-9_.:-]", "_", raw)[:128]


async def load(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    conversation_id = state["conversation_id"]
    messages = await runtime.tools.call(
        "client_liaison", "list_messages", {"conversation_id": conversation_id}
    )
    items = messages["items"]
    if not items or items[-1]["sender"] != "client":
        return {"latest_message": {}, "status": ThreadStatus.SKIPPED}
    conversations = await runtime.tools.call("client_liaison", "list_conversations", {})
    match = next(c for c in conversations["items"] if c["conversation_id"] == conversation_id)
    job = await runtime.tools.call("client_liaison", "get_job", {"job_id": match["job_id"]})
    return {
        "client_messages": items,
        "latest_message": items[-1],
        "conversation": match,
        "job": job,
        "status": ThreadStatus.RUNNING,
    }


async def classify(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    body = state["latest_message"]["body"]
    conversation_id = state["conversation_id"]
    signals = scan_job_post(body)
    if RiskFlag.SCAM in signals.flags:
        reason = "This message asks for something the platform forbids, so no reply was drafted."
        intent = IntentClassification(
            conversation_id=conversation_id,
            intent=ClientIntent.SCAM_SIGNAL,
            confidence=1.0,
            explanation=reason,
        )
        return {
            "intent": intent,
            "status": ThreadStatus.BLOCKED,
            "attention_reason": reason,
            "injected_text": signals.injected_text,
        }
    result = await runtime.llm.structured(
        "intent",
        runtime.prompt("intent"),
        delimit_untrusted("client_message", body),
        IntentClassification,
    )
    intent = result.value.model_copy(update={"conversation_id": conversation_id})
    return {"intent": intent, "injected_text": signals.injected_text, "tokens_used": result.tokens}


async def find_slots(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    slots = await runtime.tools.call("client_liaison", "free_slots", {})
    return {"proposed_slots": slots["items"]}


async def draft(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    history = "\n".join(
        f"{item['sender']}: {item['body']}"
        for item in state["client_messages"][-_MAX_HISTORY_MESSAGES:]
    )
    slots = [slot["start"] for slot in state.get("proposed_slots", [])]
    style = await runtime.memory.style_rules(state["user_id"])
    data = (
        f"Job: {state['job']['title']}\n"
        f"Intent: {state['intent'].intent.value}\n"
        f"Interview slots to offer: {slots}\n"
        f"Style rules: {style}\n\n"
        f"{delimit_untrusted('conversation', history)}"
    )
    result = await runtime.llm.structured(
        "client_liaison", runtime.prompt("client_liaison"), data, _Reply
    )
    reply = ReplyDraft(
        conversation_id=state["conversation_id"],
        body=result.value.body,
        proposed_slots=slots,
    )
    return {"reply_drafts": [reply], "tokens_used": result.tokens}


async def compliance(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    reply = state["reply_drafts"][-1]
    scan = scan_outgoing_text(reply.body)
    violations = rule_violations([scan], runtime)
    model_found, model_block, model_reason, tokens = await model_review(scan.text, runtime)
    violations += model_found
    update: dict[str, Any] = {"tokens_used": tokens}
    version = len(state["reply_drafts"])
    if model_block or scan.blocked:
        action, reason = (
            ComplianceAction.BLOCK,
            (model_reason or "This reply breaks a marketplace rule and cannot be sent."),
        )
        update["status"] = ThreadStatus.BLOCKED
    elif scan.text != reply.body:
        action, reason = ComplianceAction.REDACTED, "Prohibited content was removed."
        update["reply_drafts"] = [reply.model_copy(update={"body": scan.text})]
    else:
        action, reason = ComplianceAction.ALLOW, ""
    update["compliance_verdicts"] = [
        ComplianceVerdict(
            job_id=state["job"]["job_id"],
            draft_version=version,
            action=action,
            violations=violations,
            redacted_text=scan.text if action is ComplianceAction.REDACTED else None,
            reason=reason,
        )
    ]
    return update


async def prepare(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    result = await runtime.tools.call(
        "submission",
        "send_message",
        {
            "request": {
                "conversation_id": state["conversation_id"],
                "body": state["reply_drafts"][-1].body,
                "idempotency_key": _key(state, "send"),
                "dry_run": True,
            }
        },
    )
    return {"dry_run_preview": result["preview"], "status": ThreadStatus.AWAITING_APPROVAL}


async def approval(state: ConversationState) -> dict[str, Any]:
    """A node containing interrupt() re-runs from its start on resume, so it only reads state."""
    verdicts = state.get("compliance_verdicts", [])
    payload = {
        "conversation_id": state["conversation_id"],
        "client_message": state["latest_message"],
        "intent": state["intent"].model_dump(mode="json"),
        "reply": state["reply_drafts"][-1].model_dump(mode="json"),
        "proposed_slots": state.get("proposed_slots", []),
        "injected_text": state.get("injected_text", []),
        "compliance": verdicts[-1].model_dump(mode="json") if verdicts else None,
        "dry_run_preview": state["dry_run_preview"],
    }
    decision = ReplyDecision.model_validate(interrupt(payload))
    record = ApprovalRecord(
        action=ApprovalAction(decision.action),
        user_id=state["user_id"],
        decided_at=datetime.now(UTC),
        reason=decision.reason,
    )
    if decision.action == "reject":
        return {"approvals": [record], "status": ThreadStatus.REJECTED}
    if decision.action == "approve":
        return {
            "approvals": [record],
            "status": ThreadStatus.RUNNING,
            "selected_slot": decision.selected_slot,
        }
    reply = state["reply_drafts"][-1]
    edited = str(decision.edited_body)
    diff = "\n".join(
        difflib.unified_diff(
            reply.body.splitlines(), edited.splitlines(), "agent", "user", lineterm=""
        )
    )
    return {
        "approvals": [record.model_copy(update={"edit_diff": diff})],
        "reply_drafts": [reply.model_copy(update={"body": edited})],
        "style_edits": [{"before": reply.body, "after": edited, "diff": diff}],
        "status": ThreadStatus.RUNNING,
    }


async def send(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    """Send after approval. Stable keys make a re-run after a crash harmless."""
    latest = state.get("approvals", [])[-1:]
    if not latest or latest[0].action is not ApprovalAction.APPROVE:
        raise PermissionError("Refusing to send without an approval")
    reply = state["reply_drafts"][-1]
    sent = await runtime.tools.call(
        "submission",
        "send_message",
        {
            "request": {
                "conversation_id": state["conversation_id"],
                "body": reply.body,
                "idempotency_key": _key(state, "send"),
            }
        },
    )
    slot = state.get("selected_slot")
    details: dict[str, Any] = {"message_id": sent["message"]["message_id"]}
    if slot is not None:
        title = f"Interview: {state['job']['title']}"[:200]
        await runtime.tools.call(
            "submission",
            "create_interview_hold",
            {
                "request": {
                    "start": slot.isoformat(),
                    "duration_minutes": runtime.config.workspace.interview_minutes,
                    "title": title,
                    "idempotency_key": _key(state, "hold"),
                }
            },
        )
        await runtime.tools.call(
            "submission",
            "create_email_draft",
            {
                "request": {
                    "subject": title,
                    "body": f"Tentative interview at {slot.isoformat()}.\n\n{reply.body}",
                    "idempotency_key": _key(state, "email"),
                }
            },
        )
        details["interview_hold"] = slot.isoformat()
    return {
        "status": ThreadStatus.SENT,
        "outcomes": [
            OutcomeRecord(
                proposal_id=state["conversation"]["proposal_id"],
                status="message_sent",
                recorded_at=datetime.now(UTC),
                details=details,
            )
        ],
    }


async def learn(state: ConversationState, runtime: AgentRuntime) -> dict[str, Any]:
    source = f"{state['conversation_id']}:{state['latest_message']['message_id']}"
    await runtime.memory.record_edits(state["user_id"], source, state.get("style_edits", []))
    return {}
