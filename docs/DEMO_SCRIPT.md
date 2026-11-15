# Demo shot list

The assignment asks for a **3-minute demo video** (one job from inbox to approved proposal, one scam
caught, one injected instruction ignored, one client reply handled), a **30-second GIF** for the top
of the README, and a **screen recording** for Part D (inbox, approved proposal, client reply). One
recording can serve all three: record the full video, then cut the GIF from it.

## Before recording

- Start the stack (`make demo`, or the API, worker and web app as in the RUNBOOK) and sign in as the
  reviewer persona. Use a fresh account so the inbox starts empty.
- Add a saved search that matches the persona (for example "churn" for the AI/ML engineer).
- Close other windows; set the browser to 100% zoom. Windows: Win+Alt+R records with Xbox Game Bar,
  or use OBS.
- Keep a model quota margin: each analysed job and proposal uses model calls.

## The 3-minute video

| Time | Show | Say (one line) |
|---|---|---|
| 0:00 | The inbox, empty; click **Check for new jobs** | "GigBid scouts saved searches in a background worker." |
| 0:15 | Jobs arrive with fit, risk badges, recommended action, price and Connects cost | "Each job is analysed in parallel: requirements, risk and fit." |
| 0:35 | Open a **scam** job (risk badge, recommended skip) and its job detail page | "This one asks for free work outside the platform, so it is skipped." |
| 0:55 | Open a job with a **planted instruction** in its post; show the injection flag | "Job posts are untrusted data: the hidden instruction is flagged and ignored." |
| 1:15 | Open a good-fit job in the **proposal studio**; hover a claim to show its evidence | "Every claim cites portfolio evidence, checked before the Critic sees it." |
| 1:35 | Show the Critic and Compliance comments and the **dry-run preview** with Connects cost | "Nothing is sent yet: this is exactly what would go out, and what it costs." |
| 1:50 | Edit one sentence, then **Approve** | "Edits are diffed for style memory and re-checked by Compliance." |
| 2:05 | The **pipeline** board: the card moves to submitted, then to replied | "Marketplace events move the card." |
| 2:20 | **Messages**: the client's reply and the suggested answer; **Approve and send** | "Replies get their own graph and their own approval." |
| 2:40 | The **run viewer**: node-by-node progress, cost by step, the trace link | "Every run is traced, with tokens and cost per step." |
| 2:55 | End on the inbox | "The freelancer stays in control." |

The client reply comes from DevSim's simulated client after submission (the worker syncs events),
or use a job that already has a conversation.

## The 30-second GIF

Cut from the video: 1:15 to 1:50 (evidence on hover, the preview, approval). Save it as
`docs/images/demo.gif` and keep it under about 10 MB (for example with ezgif.com, 800 px wide,
10 frames per second).

## Optional extra for the viva

Durability (C6): with a proposal waiting for approval, stop the API, start it again, refresh the
studio (the approval is still there), approve, and show the proposal submitted exactly once. Copy
that run's LangSmith link as the crash-and-resume trace.
