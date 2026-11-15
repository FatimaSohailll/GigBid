# Signature extension: X3, Portfolio builder

> **X3.** Turn a GitHub account into case studies automatically, verified against the code, and
> feed them to the Portfolio server.

A freelancer's best evidence is often code they have never written up. GigBid reads a repository
through GitHub's official MCP server, drafts a case study from it, checks every factual sentence
against that repository's own files, removes what the files do not support, and adds the result to
the portfolio, where the Writer can cite it like any other case study.

## What it does

1. **Read the repository** (`rag/ingest/github.py`): the user enters `owner/name` in Settings or
   onboarding. The worker reads the README and source files through the GitHub MCP server in
   read-only mode, with a name allowlist, a file count limit and a size limit. Each file is indexed
   as `readme` or `code` evidence with its project and language. Repository text is untrusted data.
2. **Draft a case study** (`agent/case_builder.py`, prompt `agent/prompts/case_builder.v1.md`):
   one call to the strong model, with the README first and then code, each wrapped in tags it
   cannot close. It returns a typed `CaseStudyDraft`: title, situation, what was done, outcome and
   skills. The prompt says to state only what the files show and to say when no outcome is stated.
3. **Verify every claim against the code**: the draft's sentences go through the same claim checker
   the proposals use (`rag/verify.py`), with the repository's files as the only evidence.
   Unsupported sentences are deleted; non-factual sentences are kept.
4. **Refuse when too little survives**: a case study with fewer than two verified claims is not
   saved, so a repository that says little never becomes a vague, padded case study.
5. **Feed the Portfolio server**: the verified case study is indexed as
   `case:github:<owner>/<repo>`, with the project and skills, and its source says it was generated
   and verified against the code. From then on `search_evidence` returns it.

Configuration: `rag.github_case_studies` (on by default) and the `case_builder` model route in each
profile in `agent/config.yaml`. A failure while drafting, such as a provider limit, never blocks
the import: the repository is still indexed, and the import's status says the case study was
skipped (`indexed_case_study_skipped`) or not verified (`indexed_case_study_not_verified`).

## Why it is trustworthy

- **Nothing is invented.** The builder's own checker removes claims its repository does not
  support: a fabricated "served two million users for a national bank" is removed while the
  sentences describing the actual FastAPI and scikit-learn code are kept
  (`tests/unit/test_case_builder.py`).
- **Injection-safe.** Repository text is delimited as data, and the prompt says never to follow
  instructions inside it.
- **Read-only.** The GitHub token is fine-grained and read-only, the server runs in read-only mode,
  and only the ingestion step holds GitHub tools.

## How it is measured

`evals/runners/case_builder_eval.py`. Each DevSim persona has projects that come with a README,
code and a hand-written case study. The builder drafts a case study from the README and code alone,
and the result is compared with what the persona wrote:

| Metric | Meaning |
|---|---|
| Built rate | Share of projects with enough verified claims to save a case study |
| Verified claim rate | Share of the draft's factual sentences that its own repository supports |
| Claims removed per case study | How much the checker had to delete |
| Skill precision and recall | Extracted skills against the hand-written case study's skills |
| Tokens per case study | The cost of building one |

```bash
uv run --locked --env-file .env python -m evals.runners.case_builder_eval --limit 5
```

It writes `evals/results/case_builder_devsim.json`.

## Results

**Not run.** Each case study costs one strong-model call plus one claim check per sentence, and on
15 November 2026 the free-tier daily limits of both Groq models were spent on Parts B, C and E
first. The table is filled from the result file when the run completes, with the number of projects
actually used. What is verified so far is the mechanism, by tests: unsupported sentences are
removed and a thin case study is refused (`tests/unit/test_case_builder.py`).

| Projects | Built rate | Verified claim rate | Claims removed per case study | Skill precision | Skill recall | Tokens per case study |
|---|---|---|---|---|---|---|
| | | | | | | |

## Limits

- The checker is only as good as its evidence: a claim about code that was not among the files
  read (because of the file limits) is removed even if it is true.
- Outcomes rarely appear in repositories, so generated case studies usually say the outcome is not
  stated. That is honest, but weaker evidence than a case study with a real result.
- Verified against the code is not verified against reality: a README can overstate.
