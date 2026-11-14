# Labelling proposals for judge calibration

The assignment asks you to label proposals yourself and report Cohen's kappa between your labels
and the LLM judge (B4). Your label is the evidence that the judge can be trusted, so give your own
judgement; do not try to guess what the judge said.

## Steps

1. Export what is ready (run again whenever more proposals finish; your labels are kept):
   ```bash
   uv run --locked python -m evals.runners.judge_calibration export
   ```
2. Open `evals/datasets/judge_calibration.csv` in Excel or a text editor. Each row is one
   proposal: the job title, the job description and the cover letter.
3. For each row, read the job, then the letter, and put a whole number from 1 to 5 in
   `human_overall`. Save as CSV.
4. When you are done:
   ```bash
   uv run --locked python -m evals.runners.judge_calibration score
   ```
   This writes `evals/results/judge_calibration_devsim.json` with Cohen's kappa.

The rows are shuffled and their ids are anonymous, so you cannot tell which variant wrote which
proposal. Keep it that way: do not open `evals/datasets/judge_calibration_key.json`.

## The scale (the same question the judge answers)

**Would you, as the client, reply to this freelancer?**

| Score | Meaning |
|---|---|
| 1 | Certainly not: generic, off-topic, or makes claims you would not trust |
| 2 | Probably not: some relevance, but vague or missing what the job asked |
| 3 | Perhaps: relevant and acceptable, but nothing makes it stand out |
| 4 | Probably: specific to this job, backed by concrete past work |
| 5 | Certainly: clearly understands the problem, proves fit, easy to act on |

What to weigh, as an experienced client would:
- **Specificity:** does it address this job's actual problem, or would it fit any job?
- **Evidence:** does it back its claims with concrete past work and results?
- **Client fit:** does it cover the stated deliverables and any questions in the post?
- **Clarity:** is it short, direct and easy to act on? Do not reward length.

Judge the whole proposal; the overall score is not an average of the four. A minute or two per
proposal is enough.
