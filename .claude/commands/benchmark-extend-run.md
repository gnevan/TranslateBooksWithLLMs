# Extend an existing run with more language pairs: {{arg1}} / {{arg2}}

Take an already-evaluated benchmark run and extend it to a wider canonical
pair set (e.g. quick → standard, or standard → full). Translations
already done are **kept as-is**; only the missing (text, target_lang)
combinations are re-translated and re-evaluated. No wasted compute.

**Args:**
- `{{arg1}}` = run id to extend (8-hex from `benchmark_results/<run_id>.json`)
- `{{arg2}}` = target tier: `standard` or `full` (must be larger than the
  set the run was originally produced with)

If `{{arg1}}` is missing, list available runs via `python -m benchmark.cli list`
and ask the user to pick one.

If `{{arg2}}` is missing, ask the user — usually they'll want `full` after
running `quick`.

---

## Step 1 — Load the binding references

Read [docs/JUDGE_RUBRIC.md](../../docs/JUDGE_RUBRIC.md) and
[docs/BENCHMARK_WORKFLOW.md](../../docs/BENCHMARK_WORKFLOW.md). The rubric
is binding for any new evaluations.

---

## Step 2 — Inspect the existing run

Read the run JSON to identify its model, current pair coverage, and
existing scores:

```
python -c "import json,sys; sys.stdout.reconfigure(encoding='utf-8'); d=json.load(open('benchmark_results/{{arg1}}.json',encoding='utf-8')); print('Model(s):', d['models']); print('Languages so far:', d['languages']); print('Total results:', len(d['results'])); print('Scored:', sum(1 for r in d['results'] if r['scores'])); print('Unscored:', sum(1 for r in d['results'] if not r['scores']))"
```

If `Unscored > 0`, **stop**: the run has translations that haven't been
scored yet. Tell the user to first finish the existing evaluation (via
`/benchmark-test-model` resume flow or manual `dump_for_evaluation` →
`apply_evaluations` cycle) before extending. We don't want to mix
incomplete batches.

---

## Step 3 — Determine the provider

The run JSON doesn't store the provider explicitly. Infer it from the
model name (a name like `gemma3:27b` with a colon is Ollama; a slash like
`anthropic/claude-haiku-4-5` is OpenRouter; a dotted name like
`gemini-3-flash-preview` is most often Poe).

Confirm with the user via `AskUserQuestion` before proceeding — we need
the right provider to translate the new pairs.

---

## Step 4 — Translate only the delta

```
python -m benchmark.cli run -p <provider> -m <model> --no-evaluate --pair-set {{arg2}} --resume {{arg1}}
```

The runner skips already-completed `(text, target_lang, model)` triples
and only translates the new ones. Capture the printed count
(`Jobs to process: N`) — this is the number of new translations.

If `Jobs to process: 0`, tell the user the run already covers the entire
`{{arg2}}` tier and there's nothing to extend. Stop.

---

## Step 5 — Dump and score the new translations

```
python scripts/dump_for_evaluation.py {{arg1}} --batch-size 15 --out plan/eval_{{arg1}}_extend.md
```

`dump_for_evaluation.py` only emits results with `scores: null` — i.e.
the ones we just translated. Read every batch and score them per the
rubric (start at 10, deduct, hard cap 9.0, etc.).

---

## Step 6 — Apply scores

Write `plan/eval_{{arg1}}_extend_rubric_v1.json` with one entry per new
`eval_id`, then:

```
python scripts/apply_evaluations.py {{arg1}} plan/eval_{{arg1}}_extend_rubric_v1.json --judge-id <judge-id>
```

Use the matching judge id (`claude-opus-4-7-rubric-v1`, etc. — same
convention as `/benchmark-test-model`).

---

## Step 7 — Report and propose the new submission

Tell the user:

- Number of new translations evaluated.
- Per-pair averages on the new pairs (group by target language).
- Whether the new tier reveals weaknesses (e.g. low scores on `en→bn`
  when the model was strong on European pairs).
- Updated overall average across the **whole** run (old + new combined).

Then propose the next steps:

```
# 1. Inspect the existing submission for this model
ls benchmark/data/submissions/ | grep <model-slug>

# 2. Either delete the old (smaller) submission, or keep it as a separate
#    observation. Deleting is cleaner — the new submission supersedes it.
rm benchmark/data/submissions/<old-date>_<user>_<model-slug>.json

# 3. Submit the expanded run
python -m benchmark.cli submit benchmark_results/{{arg1}}.json --by github:<user> --provider <provider> --judge-id <judge-id>

# 4. Republish the wiki
/benchmark-publish-wiki
```

---

## Important guardrails

- **Don't auto-submit.** This skill stops at the report; the user picks
  whether to replace the old submission, keep both, or discard.
- **Don't shrink the tier.** Going `full → standard` would not "remove"
  translations; it'd just produce no new ones. The runner ignores
  shrinkage gracefully but it's a no-op.
- **Don't change the judge between extensions.** If the run was scored by
  Claude Opus 4.7, score the new pairs with the same judge to keep the
  data internally consistent. To rejudge with a stronger model, use
  `/benchmark-rescore-submission` instead.
