# Context-window evaluation

This repository holds two kinds of things:

- **Recipes**: Q&A agents that each compose the model's context window in a different way (which corpus they read, how many parts are retrieved, semantic or hybrid search, whether parts are passed in full or summarized, how figures are brought in, and so on).
- **One judge**: a judge agent plus a scoring script. Together they produce metrics for an answer, so that recipes can be compared with each other.

The judge is not a product feature and does not answer questions. Its only purpose is to provide metrics that tell us how well a given way of composing the context window works. `chartbook_v2_agent_chunk3000` is one recipe, the first one here. More recipes will be added next to it, and every one of them is measured with the same judge.

This is an internal working repository. It targets a Vectara staging account and names resources that exist there.

## What the judge measures

For each answer a recipe produced, the judge reports:

| Metric | What it is | Produced by |
|---|---|---|
| `judge_score` | One score from 0 to 3 covering accuracy, groundedness and relevance, graded only against the ground-truth documents | the judge agent `qa_answer_judge` |
| `judge_status` | `scored`, or a status meaning no judgement was made: `judge_error` or `ground_truth_lacks_answer` | the judge agent |
| `hhem_score` | 0 to 1, how consistent the answer is with the text of the ground-truth documents (Vectara HHEM, `hhem_v2.3`). Text only: it cannot see values that exist only inside a figure | the script, via `POST /v2/evaluate_factual_consistency` |
| `qa_total_input_tokens`, `qa_total_output_tokens` | Tokens of the Q&A session added up over all of its traces. This is what the session cost in tokens | the script, via `GET /v2/agent_analytics/traces` |
| `qa_context_tokens`, `qa_context_window` | Size of the session's most recent model call and the model's window, which shows how full the context window got | the script, from the session object |
| `qa_model_seconds` | Summed duration of the session's traces | the script, via `GET /v2/agent_analytics/traces` |
| `qa_cost_usd` | Input tokens times `--price-in` plus output tokens times `--price-out`, both in USD per million tokens. Empty unless both prices are given | the script |
| `error` | Anything that went wrong for that row. A row with a non-empty `error` must be left out of comparisons | the script |

The judge agent sees only the question, the answer and the ground-truth PDF files. It never sees what the recipe retrieved, so a recipe that retrieves the wrong documents cannot make its answer look grounded.

## Layout

```
README.md
judge/
  README.md                             how the judge works and how to deploy it
  agent.json                            judge agent definition (qa_answer_judge)
  prompt.md                             the judge agent's instructions and scoring rubric
  output_schema.json                    structured verdict the judge agent returns
  tools/fetch_qa_session.py             tool code: returns only the question and final answer of a Q&A session
  tools/fetch_qa_session.tool.json      tool definition, ready to create
  judge_answers.py                      scoring script: judge score, HHEM, tokens, cost
recipes/
  README.md                             what a recipe is, and the list of recipes
  chartbook_v2_agent_chunk3000/         first recipe: README.md (recipe card), agent.json, prompt.md
examples/
  answers.example.csv                   shape of the scoring script's input
scripts/
  export_agent.py                       pull an agent's agent.json and prompt.md from the API
```

## Evaluating a recipe

1. Ask the recipe agent your questions, one session per question, and note each session id.
2. List them in a CSV with the ground-truth PDF for each question (see `examples/answers.example.csv`):

```
id,qa_agent,qa_session,ground_truth_files
q1,chartbook_v2_agent_chunk3000,ase_1a2b_3c4d,example_paper.pdf
```

3. Run the scoring script. It reads the question and the final answer from each session, sends them with the ground-truth PDFs to the judge agent, and adds HHEM and token usage:

```bash
python judge/judge_answers.py --input answers.csv --pdf-dir /path/to/ground_truth_pdfs --out judged.csv
```

Add `--price-in` and `--price-out` to fill the cost column. The script needs `VECTARA_API_KEY` in the environment (see below).

A single answer can also be judged by hand: open a session on `qa_answer_judge`, attach the ground-truth PDFs, and send `agent: <recipe agent key>, session: <session id>`. See [judge/README.md](judge/README.md).

## Adding a recipe

1. Build the new Q&A agent on the platform.
2. Export it: `python scripts/export_agent.py <agent_key> recipes/<agent_key>`
3. Add a `README.md` recipe card in that folder stating what is different about how this recipe composes the context window, and add a row to [recipes/README.md](recipes/README.md).
4. Run the same question set through it and judge it as above.

## Environment and secrets

- The scripts default to the staging API (`https://api.vectara.dev/v2`); set `VECTARA_BASE_URL` to point them elsewhere. That variable does not affect the judge agent: its fetch tool has its own base URL inside `judge/agent.json`, which must be changed too when deploying to another environment (see [judge/README.md](judge/README.md)).
- No API key is stored in this repository, and none should ever be committed. Scripts read `VECTARA_API_KEY` from the environment. To keep the key out of your shell history, set it with `read -rs VECTARA_API_KEY && export VECTARA_API_KEY` and paste the key at the silent prompt.
- Input CSVs, result CSVs and ground-truth PDFs are ignored by git on purpose. They hold session ids, answers and source documents that do not belong in the repository.
- Agent definitions reference a corpus, models and tool ids that exist on the staging account. Each README lists what its agent depends on.

## Status

As of the first commit:

- Checked: each `agent.json` here, posted under a temporary key, recreates an agent identical to the live one (prompt, tools, model, settings). The fetch tool, run against a real Q&A session, returns the question and final answer and nothing else. HHEM was tried against a whole document: about 0.98 for a sentence taken from it and about 0.0001 for a fabricated claim.
- Not yet recorded here: a complete judging run from this repository, end to end.
- The judge agent's prompt is its first version. It has been reviewed but not calibrated against human-graded answers, so treat absolute scores with care and rely on comparisons between recipes judged on the same questions.
