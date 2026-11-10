# Data Card

## Status

All data used so far is **synthetic and self-generated (DevSim)**. The instructor's GigSim, persona
corpora and dev set were not in the starter kit. No result here is a GigSim result, and none comes
from real freelancers or clients.

## How it is made

`make seed` runs `devsim/src/devsim/datagen/` (`catalog.yaml`, `corpus.py`, `jobs.py`, `pdf.py`) and
writes to `data/processed/devsim/`, which is git-ignored. Text comes from templates and a catalog
written for this project.

## Contents

| Data | Size | Location under `data/processed/devsim/` |
|---|---|---|
| Personas | 3: AI/ML engineer, full-stack developer, data analyst | `personas/` |
| Portfolio items | 645 in total, 215 per persona | `personas/<name>/index.json` |
| Jobs | 750 | `marketplace/jobs.json` |
| Clients, with hidden preferences | 450 | `marketplace/clients.json` |
| Historical jobs for pricing | 1,500 | `marketplace/history.json` |
| Gold-labelled dev jobs | 600, 200 per persona | `dev_set/` |

Each persona has 150 past proposals labelled won, replied or ignored, 16 case studies, 16 READMEs,
16 code files, 16 client reviews and 1 PDF CV.

## Labels

Each dev job has a bid-or-skip decision, a job type, scam and injection flags, a fair price band,
and per-requirement evidence. Job types are good fit, partial fit, other field, low budget, vague,
scam and two planted-injection types.

Labels are derived from the same catalog that generates the portfolio. Requirement evidence lists
case studies, READMEs and code that show a skill. Job-level relevance is limited to the three
best-matching projects.

## Known limitations

- Labels and text share one generator, so scores measure performance on this corpus only.
- Text is more regular than real posts and portfolios.
- Past proposals are 70% of the corpus and are never gold evidence, which lowers retrieval scores.
- Some requirements have more than five correct items, so Recall@5 cannot reach 1. The ceiling is
  0.8241 per requirement and 0.8507 per job.
- Simulated client replies use a seeded rule on hidden preferences, not GigSim's LLM clients.
  The rates describe that rule.

## Privacy and use

The data is synthetic and contains no personal information. Uploaded portfolios and connected
GitHub repositories are stored per user and were not used in any evaluation. The hidden test set
never enters this repository.
