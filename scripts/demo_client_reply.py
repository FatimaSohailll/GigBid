"""Make DevSim's client reply to the freelancer's latest submitted proposal, for a demo recording.

DevSim's simulated client replies only some of the time, so a demo cannot rely on it. This sends
one client message through DevSim's admin API; GigBid then picks it up like any other reply (the
worker syncs events every minute) and the conversation graph drafts an answer for approval.

Inside the Docker stack:
  docker compose -f infra/docker-compose.yml --env-file .env exec devsim \
      python -m scripts.demo_client_reply [persona] [message]

It reads DEVSIM_API_TOKENS and DEVSIM_ADMIN_TOKEN from the environment, never prints a token, and
only talks to the simulator (DEVSIM_URL, default http://localhost:8081). It does nothing on Upwork.
"""

import os
import sys

import httpx

DEFAULT_MESSAGE = (
    "Thanks for the proposal, it looks like a good match. Could you tell me how you would approach "
    "the first week, and are you free for a short call on Thursday or Friday?"
)


def persona_token(persona: str) -> str:
    for pair in os.environ.get("DEVSIM_API_TOKENS", "").split(","):
        token, _, owner = pair.partition("=")
        if owner.strip() == persona and token.strip():
            return token.strip()
    raise SystemExit(f"No DevSim token for persona {persona!r} in DEVSIM_API_TOKENS.")


def main(argv: list[str]) -> int:
    persona = argv[0] if argv else "ai_ml_engineer"
    message = argv[1] if len(argv) > 1 else DEFAULT_MESSAGE
    admin = os.environ.get("DEVSIM_ADMIN_TOKEN")
    if not admin:
        raise SystemExit("DEVSIM_ADMIN_TOKEN is not set.")
    base = os.environ.get("DEVSIM_URL", "http://localhost:8081")
    with httpx.Client(base_url=base, timeout=15) as client:
        mine = client.get(
            "/v1/proposals", headers={"Authorization": f"Bearer {persona_token(persona)}"}
        )
        mine.raise_for_status()
        submitted = [p for p in mine.json() if p.get("submitted_at")]
        if not submitted:
            print(f"{persona} has no submitted proposal yet. Approve one in GigBid first.")
            return 1
        latest = max(submitted, key=lambda p: p["submitted_at"])
        sent = client.post(
            f"/admin/proposals/{latest['proposal_id']}/client-message",
            headers={"Authorization": f"Bearer {admin}"},
            json={"body": message},
        )
        sent.raise_for_status()
    print(f"Client replied to proposal {latest['proposal_id']} (job {latest['job_id']}).")
    print("GigBid shows it in Messages within about a minute.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
