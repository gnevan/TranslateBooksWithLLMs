# Benchmark Workflow — End-to-End Procedure

The canonical playbook for producing translations and evaluating them via
**manual LLM-as-judge** (Claude Code, Cursor, etc.). Following this doc keeps
results comparable across sessions and contributors.

If you want a fully automated path with an auto-judge (no human in the loop),
see [CONTRIBUTING_BENCHMARK.md](CONTRIBUTING_BENCHMARK.md) instead.

---

## Claude Code skills (local slash commands)

Three skills automate the full workflow when driven from Claude Code. Each is
a Markdown file under `.claude/commands/` (gitignored — local to each
maintainer's machine). Once present, they appear as `/`-prefixed commands in
the Claude Code UI.

### `/benchmark-test-model <provider> <model-id>`

Run the entire flow for one model end-to-end:

1. Pre-validates the model id against the provider's `/models` API (catches
   typos and unpulled Ollama models before wasting a 7-minute translation
   pass).
2. Produces translations on the 8 canonical pairs (`--no-evaluate`).
3. Dumps the evaluation brief and reads it.
4. Scores every translation per [JUDGE_RUBRIC.md](JUDGE_RUBRIC.md): start at
   10, deduct per the penalty table, respect §4 hard ceilings.
5. Writes `plan/eval_<RUN_ID>_rubric_v1.json`.
6. Applies the scores via `apply_evaluations.py` with the right judge_id.
7. Reports per-pair averages, top/bottom 3, and a copy-pasteable `submit`
   command.

Examples:

```text
/benchmark-test-model poe gemini-3-flash-preview
/benchmark-test-model ollama gemma3:27b
/benchmark-test-model openrouter anthropic/claude-haiku-4-5
```

If args are missing, the skill asks for them via `AskUserQuestion`.

The skill stops before the actual `submit` step. The maintainer reviews the
report and runs `submit` manually once happy.

### `/benchmark-rescore-submission <submission-path>`

Re-evaluate an existing submission with the **current** Claude version as a
fresh judge — without re-translating. Useful when a stronger model becomes
available and you want to refresh historical scores.

Reads `benchmark/data/submissions/<file>.json`, reconstructs a runnable JSON
via `submission_to_run.py` (translations preserved, `scores: null`), then
runs the same dump → score → apply pipeline as `benchmark-test-model`.
Outputs a side-by-side comparison report (old judge avg vs new judge avg,
biggest deltas).

Example:

```text
/benchmark-rescore-submission benchmark/data/submissions/2026-05-09_hydropix_gemma3-27b.json
```

The skill stops before submitting the new observation; the maintainer
decides whether to publish it as a second `n_obs` on the wiki or discard.

### `/benchmark-extend-run <run-id> <new-tier>`

Take a run produced under one tier (e.g. `quick`) and extend it to a
larger tier (`standard` or `full`) without re-translating the pairs
already covered. Only the delta is translated and evaluated.

Concretely, going from `quick` (8 pairs, 45 translations) to `full`
(28 pairs, ~245 translations) on a model that's already in `quick` only
requires translating the ~190 new translations. The old 45 stay
untouched.

Example:

```text
/benchmark-extend-run 06e8de45 full
```

Behind the scenes the skill runs:

```text
python -m benchmark.cli run -p <provider> -m <model> --no-evaluate --pair-set <new-tier> --resume <run-id>
```

— and `_generate_jobs` filters out the (text, target_lang, model)
triples already present in the run. Then it dumps only the unscored new
translations, scores them, and applies. The user replaces the old
submission with the expanded one when ready.

### `/benchmark-publish-wiki`

Regenerate and push the wiki from whatever's currently in
`benchmark/data/submissions/`. No args.

Use this when:

- The auto `publish-wiki.yml` workflow can't push (typically because
  `WIKI_PUSH_TOKEN` isn't configured in the repo secrets).
- You want to publish immediately rather than waiting for the workflow.
- You want to verify the v2 wiki layout locally before pushing.

Idempotent: if no submission has changed since the last publish, the skill
detects "no changes" and exits cleanly without committing.

The skill always preserves `Archive-*` pages from the v1 archive — its
cleanup globs explicitly exclude them.

---

## Typical maintainer session

After producing a few benchmark runs, the workflow is:

```text
# 1. Test each new model — one slash command per model
/benchmark-test-model poe gpt-5-mini
[review report]

/benchmark-test-model poe mistral-medium-3.1
[review report]

# 2. Submit each evaluated run (the skill suggests the exact command)
python -m benchmark.cli submit benchmark_results/<RUN_ID_1>.json --by github:<user> --provider poe --judge-id claude-opus-4-7-rubric-v1
python -m benchmark.cli submit benchmark_results/<RUN_ID_2>.json --by github:<user> --provider poe --judge-id claude-opus-4-7-rubric-v1

# 3. Commit + push the new submissions
git add benchmark/data/submissions/
git commit -m "submit(benchmark): gpt-5-mini, mistral-medium-3.1"
git push origin main

# 4. Republish the wiki (only needed until WIKI_PUSH_TOKEN is configured)
/benchmark-publish-wiki
```

Once `WIKI_PUSH_TOKEN` is set in the repo secrets, step 4 disappears — the
auto workflow takes over.

---

## Sharing the skills with other contributors

`.claude/commands/*.md` is gitignored by default. To share, add a per-file
exception:

```gitignore
.claude/
!.claude/commands/benchmark-*.md
```

Then `git add .claude/commands/benchmark-test-model.md ...` and commit. Other
contributors who pull and use Claude Code will see the same slash commands.

---

## The canonical pair sets

Three fixed tiers live in [benchmark/canonical_pairs.py](../benchmark/canonical_pairs.py)
and are addressable via `--pair-set quick|standard|full`. Each is a
**unidirectional** set: we pick the direction with strongest real-world
demand for each language and never include both `en→fr` and `fr→en`.

### Quick (8 pairs) — the default

Fast iteration on a new model. ~45 translations, ~10 min judge time.

```text
en:zh-Hans  en:es  en:fr  en:vi  ja:en  ko:en  zh-Hans:en  ja:zh-Hans
```

Rationale (from the market study at v2 launch):

- `en→zh-Hans`: #1 real demand — Chinese users importing foreign content.
- `en→es`: 500M+ speakers, high-volume baseline.
- `en→fr`: quality baseline (DeepL excellent here, useful comparison point).
- `en→vi`: underserved by mainstream tools, growing market.
- `ja→en`: manga / light novel community.
- `ko→en`: k-literature surge (+285% in 2024).
- `zh-Hans→en`: Chinese webnovel / academic flow.
- `ja→zh-Hans`: documented manga industry flow into China.

### Standard (16 pairs) — recommended default for thorough evaluation

Quick + 8 outbound English to major target languages. ~125 translations,
~30 min judge time.

Adds: `en→{de, pt, ja, ko, ru, it, ar, hi}`.

Covers the major Indo-European, East Asian, Semitic, and Indo-Aryan
language families. Use this when you want a benchmark that's representative
of real general-purpose translation usage.

### Full (28 pairs) — deep evaluation

Standard + 12 broad-coverage additions for linguistic diversity and
underserved markets. ~245 translations, ~60 min judge time.

Adds:

- European diversity: `en→{nl, pl, sv, da, el, tr}`
- Asian gaps (DeepL doesn't have these): `en→{th, id, bn, ta}`
- RTL: `en→he`
- Cross-Asian: `zh-Hans→ja` (Chinese light novels into Japanese)

Use this for a thorough benchmark when budget allows — useful to surface
weaknesses on rare scripts (Bengali, Tamil), agglutinative languages
(Turkish), and underserved markets.

### Custom pair sets

For one-off experiments, use `--pairs SRC:TGT [SRC:TGT ...]` instead of
`--pair-set`. The two flags are mutually exclusive. Custom sets don't
contribute comparable data to the wiki — submitted runs should use one of
the three canonical tiers if they're meant to be aggregated alongside
others.

### Choosing a tier

| Goal | Tier |
|---|---|
| Validate a new model quickly, iterate on prompts | quick |
| Add a model to the wiki on the most demanded pairs | standard |
| Deep dive on a frontier model, surface weaknesses | full |
| Re-run the same model on more languages later | resume with `--pair-set <larger>` (translations from the smaller set are skipped) |

---

## Phase 1 — Produce translations

For **each model** to benchmark, run one of:

```bash
# Local Ollama
python -m benchmark.cli run -p ollama -m <model-id> --no-evaluate \
  --pairs en:zh-Hans en:es en:fr en:vi ja:en ko:en zh-Hans:en ja:zh-Hans

# Poe (uses POE_API_KEY from .env)
python -m benchmark.cli run -p poe -m <model-id> --no-evaluate \
  --pairs en:zh-Hans en:es en:fr en:vi ja:en ko:en zh-Hans:en ja:zh-Hans

# OpenRouter (uses OPENROUTER_API_KEY from .env)
python -m benchmark.cli run -p openrouter -m <model-id> --no-evaluate \
  --pairs en:zh-Hans en:es en:fr en:vi ja:en ko:en zh-Hans:en ja:zh-Hans
```

**Critical flags:**
- `--no-evaluate` — skips the auto-judge so the run finishes faster and we
  control the scoring entirely in Phase 2.
- `--pairs` — uses the explicit pair list (different source languages allowed).
  Without it, the runner defaults to English source and ignores `ja/ko/zh-Hans`
  texts.

The CLI prints a `<RUN_ID>` (8 hex chars). Note it. The output is
`benchmark_results/<RUN_ID>.json` with `scores: null` on every result.

**Expected volume per run:** ~45 translations on the canonical 8 pairs (10 EN
texts × 4 EN→X targets + 4 X→en pairs from non-EN texts).

---

## Phase 2 — Manual evaluation in Claude Code

This phase is collaborative. The user drives the CLI; the assistant (Claude)
acts as the judge.

### What the user types

Open a Claude Code session and prompt:

> "Evaluate benchmark run `<RUN_ID>`. Follow `docs/BENCHMARK_WORKFLOW.md` and
> apply `docs/JUDGE_RUBRIC.md`."

Then let the assistant work. No further commands needed from the user during
the evaluation itself.

### What the assistant must do (Claude protocol)

Execute these steps mechanically, in order:

#### Step 1 — Load both reference documents
Read [docs/JUDGE_RUBRIC.md](JUDGE_RUBRIC.md) (penalty table, scale anchors,
ceilings, dispersion rule) and this file in full. Treat them as binding.

#### Step 2 — Dump the brief
```bash
python scripts/dump_for_evaluation.py <RUN_ID> --batch-size 15 --out plan/eval_<RUN_ID>.md
```
Produces one or more `plan/eval_<RUN_ID>_batch*.md` files.

#### Step 3 — Read every batch in full
Each translation has a stable `eval_id`, the source text, the model's output,
and the declared `challenges`. Don't skim — score each translation on its own
merits.

#### Step 4 — Score using the rubric
For each translation:
- Identify all errors (contresens, untranslated source words, script
  mismatches, grammar errors, lost rhetoric, period-wrong vocabulary…).
- Apply the penalty table (rubric §3): `accuracy`, `fluency`, `style` start
  at 10, deduct per detected issue.
- Compute `overall` as a holistic call constrained by rubric §4 (hard
  ceiling 9.0 without human reference comparison; if any dimension < 6.0,
  overall ≤ 6.0; etc.).
- Write a 1–2 sentence `feedback` documenting the deductions (rubric §7).
  Specific, auditable, with the penalty values.

If multiple model runs cover the same `(text_id, target_lang)` triples (cross-
model comparison), apply rubric §5: rank the N outputs, enforce ≥0.3 point
difference between adjacent ranks on `overall`.

#### Step 5 — Write the JSON reply
Single file: `plan/eval_<RUN_ID>_rubric_v1.json`. Format is a flat JSON array:

```json
[
  {"eval_id": "<10-hex>", "scores": {
    "accuracy": 0.0, "fluency": 0.0, "style": 0.0, "overall": 0.0,
    "feedback": "..."
  }},
  ...
]
```

One object per `eval_id` from the brief. Don't omit any.

#### Step 6 — Apply the scores
```bash
python scripts/apply_evaluations.py <RUN_ID> plan/eval_<RUN_ID>_rubric_v1.json \
  --judge-id <judge-model>-rubric-v1
```

Where `<judge-model>` is the LLM acting as judge:
- `claude-opus-4-7` (current Claude Code default for Bruno's setup)
- `claude-sonnet-4-6`
- `gemini-3-pro`
- `gpt-5`

The full `judge_id` therefore looks like `claude-opus-4-7-rubric-v1`. This
identifier appears on the wiki next to every score.

The script is idempotent — rerunning is safe.

#### Step 7 — Report distribution
End the turn with:
- Per-pair averages (`en→zh-Hans`, `ja→en`, etc.)
- Global average and number of evaluations
- Notable findings (best/worst translations, recurring failure modes)
- Comparison with prior runs if available

---

## Phase 3 — Submit and publish

Once a run is fully evaluated, convert it to a community submission:

```bash
python -m benchmark.cli submit benchmark_results/<RUN_ID>.json \
  --by github:<your-username> \
  --provider <ollama|poe|openrouter|openai> \
  --judge-id <judge-model>-rubric-v1
```

This produces a validated submission at:
```
benchmark/data/submissions/<DATE>_<USER>_<MODEL-SLUG>.json
```

Verify the schema:
```bash
python scripts/validate_submission.py benchmark/data/submissions/<DATE>_<USER>_<MODEL-SLUG>.json
```

Commit and push. The `publish-wiki` GitHub Action regenerates the wiki on
merge to `main`. The aggregator merges multiple submissions covering the same
`(model, text, target_lang)` triple by **median** and surfaces an `n_obs`
column on the wiki.

---

## File map

| Path | Role | Tracked in git? |
|---|---|---|
| [docs/JUDGE_RUBRIC.md](JUDGE_RUBRIC.md) | The rubric the judge applies (penalty table, scale anchors) | ✅ |
| [docs/BENCHMARK_WORKFLOW.md](BENCHMARK_WORKFLOW.md) | This doc — the procedure | ✅ |
| `benchmark_results/<RUN_ID>.json` | Translations + scores in place | ❌ (gitignored) |
| `plan/eval_<RUN_ID>_*.md`, `plan/eval_<RUN_ID>_*.json` | Per-session brief and reply | ❌ (gitignored) |
| `benchmark/data/submissions/*.json` | Final submissions, schema-validated | ✅ |
| [benchmark/schemas/submission.schema.json](../benchmark/schemas/submission.schema.json) | Strict schema for submissions | ✅ |

---

## What changes between sessions

The only thing that drifts session-to-session is the **judge model**. If a
future Claude version (e.g. Opus 5) re-evaluates the same translations:
- Use a different `judge_id`: `claude-opus-5-rubric-v1`.
- The aggregator considers it as a different observation; both scores end up
  in the wiki under the same `(model, text, lang)` cell, separated by judge.
- The rubric version stays `v1` as long as `docs/JUDGE_RUBRIC.md` doesn't
  change. If you bump the rubric to v2, also bump the judge_id suffix.

---

## Switching the live wiki from v1 to v2

The v2 system (this doc) coexists with the v1 wiki only through a one-shot
**archive step**: every existing v1 wiki page is renamed with an `Archive-`
prefix, internal cross-page links are rewritten so the archived pages still
work standalone, and an `Archive-Index.md` is published as the entry point.
After that, the v2 publish-wiki workflow can run without colliding.

Run this **before merging the v2 branch to `main`**:

```bash
# Dry-run first to see what would be renamed
python scripts/archive_v1_wiki.py --dry-run

# Apply (commits + pushes to the wiki repo)
python scripts/archive_v1_wiki.py --message "Archive v1 benchmark"

# If you want to inspect the commit before pushing:
python scripts/archive_v1_wiki.py --no-push
# ...then `git -C .wiki_repo_archive push` when ready
```

What the script does:

1. Clones the wiki repo into `.wiki_repo_archive/` (gitignored).
2. Lists every `*.md` at the wiki root that is not already prefixed `Archive-`.
3. Inside each one, rewrites cross-page links so `[Foo](Bar)` becomes
   `[Foo](Archive-Bar)` for any link whose target is one of the pages being
   archived.
4. Renames the files (`Home.md` → `Archive-Home.md`, `Language-French.md` →
   `Archive-Language-French.md`, etc.).
5. Generates `Archive-Index.md` listing every archived page, grouped by
   category (landing, cross-cutting tables, per-language, per-model).
6. Commits and pushes.

The v2 home template (`benchmark/wiki/templates/home.md.j2`) already includes
a banner pointing to `Archive-Index`, so once the v2 wiki regenerates after
the merge, visitors land on the v2 home and can click through to the
archived v1 if they need historical scores.

The `publish-wiki.yml` workflow's cleanup step only deletes `Home.md`,
`All-Languages.md`, `All-Models.md`, `Language-*.md`, `Model-*.md` — it does
not touch `Archive-*` pages, so the archive survives every future republish.

---

## Rescoring an existing submission with a fresh judge

If a stronger judge becomes available later (or you just want a second
opinion), the model **outputs are preserved verbatim** in
`benchmark/data/submissions/<file>.json` and can be re-evaluated without
re-running the translations.

The full rescore path:

```bash
# 1. Reconstruct a runnable JSON from the historical submission
python scripts/submission_to_run.py benchmark/data/submissions/<file>.json
# captures: RUN_ID=<new-id>

# 2. Standard manual eval flow (steps 4-6 of Phase 2 above)
python scripts/dump_for_evaluation.py <RUN_ID> --batch-size 15 --out plan/eval_<RUN_ID>.md
# ... judge reads, scores, writes plan/eval_<RUN_ID>_rubric_v1.json ...
python scripts/apply_evaluations.py <RUN_ID> plan/eval_<RUN_ID>_rubric_v1.json --judge-id <new-judge-id>

# 3. Submit as a new observation under the SAME model+provider
python -m benchmark.cli submit benchmark_results/<RUN_ID>.json \
  --by github:<original-submitter> \
  --provider <original-provider> \
  --judge-id <new-judge-id>
```

Or, if you're driving from a Claude Code session, the `/benchmark-rescore-submission`
skill chains all of this automatically — only Step 3 (submit) stays manual.

The aggregator merges both observations into a single wiki cell with `n_obs=2`
and the **median** of the scores. To audit the difference between the original
and the new judge on the wiki, look at the per-language pages — they list each
observation separately when there are multiple.

---

## Common edge cases

**The run has scores already (auto-judge ran first).**
`dump_for_evaluation.py` only dumps results with `scores: null`. If you want
to re-evaluate translations that already have scores, you'll need to clear
them first (or add a `--rescore-all` flag — not currently implemented).

**A pair like `ko:en` produces 0 translations.**
Check that `benchmark/data/languages/en.yaml` and the source text exists in
`benchmark/data/reference_texts/ko/`. Missing language entries are silently
dropped from `--pairs`.

**The submission CLI complains "no usable results".**
The translation provider failed for this model. Check `benchmark_results/<RUN_ID>.json`
for `error` fields and rerun the `benchmark.cli run` command to fix the
provider config.

**The judge_id is wrong on a submission already pushed.**
Re-apply with the correct `--judge-id` via `apply_evaluations.py`, then
re-`submit`, then commit the corrected submission file (overwrites the old
one in `benchmark/data/submissions/`).

---

## Why this exists

Without this doc, every new contributor (or new Claude session) reinvents the
procedure, drifts off the canonical 8 pairs, uses a different judge_id format,
or skips the rubric — and the wiki accumulates incomparable scores. Following
this playbook keeps the benchmark scientifically meaningful over time.
