"""A deterministic stand-in for GigSim's simulated clients.

It uses each client's hidden preferences to decide whether a submitted proposal gets a reply or a
hire. Rates measured here describe this rule, not real clients and not the official GigSim.

The random draw depends only on the seed, job and freelancer. Two pipelines bidding on the same
job face the same draw, so a difference in reply rate comes from the proposals themselves.
"""

import random
import re
from dataclasses import dataclass

from devsim.models import ClientRecord, Job, Proposal

_GENERIC_OPENING = re.compile(
    r"^\s*(dear|hi there|hello|greetings|i am writing|i'm writing|i am an? |i have read|to whom)",
    re.IGNORECASE,
)
_OVER_CEILING = 0.2
_OVER_BUDGET = 0.6
_GENERIC_DISLIKED = 0.5
_NO_PROOF = 0.6
_PROOF_BONUS = 1.2
_NO_SKILL_NAMED = 0.7
_SCAM_ACTOR = 0.0
_MAX_PROBABILITY = 0.95
_HIRE_GIVEN_REPLY = 0.35
_BUDGET_TOLERANCE = 1.1


@dataclass(frozen=True)
class ClientReaction:
    reply_probability: float
    replied: bool
    hired: bool


def reply_probability(job: Job, client: ClientRecord, proposal: Proposal) -> float:
    preferences = client.hidden_preferences
    letter = proposal.cover_letter
    lowered = letter.lower()
    names_skill = any(skill.lower() in lowered for skill in job.required_skills)
    has_proof = names_skill and any(character.isdigit() for character in letter)

    probability = float(preferences.get("reply_propensity", 0.5))
    if preferences.get("scam_actor"):
        probability *= _SCAM_ACTOR
    if proposal.bid_amount > float(preferences.get("budget_ceiling_usd", float("inf"))):
        probability *= _OVER_CEILING
    elif proposal.bid_amount > job.budget.max * _BUDGET_TOLERANCE:
        probability *= _OVER_BUDGET
    if preferences.get("dislikes_generic_openings") and _GENERIC_OPENING.match(letter):
        probability *= _GENERIC_DISLIKED
    if preferences.get("values_evidence"):
        probability *= _PROOF_BONUS if has_proof else _NO_PROOF
    if not names_skill:
        probability *= _NO_SKILL_NAMED
    return min(probability, _MAX_PROBABILITY)


def react(job: Job, client: ClientRecord, proposal: Proposal, seed: int) -> ClientReaction:
    probability = reply_probability(job, client, proposal)
    draw = random.Random(f"{seed}:{job.job_id}:{proposal.freelancer_id}")
    replied = draw.random() < probability
    hired = draw.random() < _HIRE_GIVEN_REPLY
    return ClientReaction(round(probability, 4), replied, replied and hired)
