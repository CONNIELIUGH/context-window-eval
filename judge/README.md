# The judge

The judge has two parts, and its only purpose is to provide metrics for comparing recipes. It does not answer questions itself.

- **The judge agent** (`qa_answer_judge`) grades one answer against ground-truth documents and returns a single 0 to 3 score with its reasoning.
- **The scoring script** (`judge_answers.py`) runs the judge agent over a list of answers and adds the metrics a model cannot produce: the HHEM score and the Q&A session's token usage and cost.

## The judge agent

- Model: `claude-sonnet-4-6`, temperature 0. It deliberately uses a different, stronger model than the recipes it grades.
- It sees the question, the answer, and the ground-truth PDF files. It never sees what the recipe retrieved.

### Input

Attach the ground-truth PDF file or files to the turn, and give the answer to grade in one of two forms.

As a reference to the recipe's session (the judge agent fetches the question and the final answer itself):

```
agent: chartbook_v2_agent_chunk3000, session: ase_1a2b_3c4d
```

Or as text:

```
QUESTION:
<the question>

ANSWER:
<the recipe's answer>
```

If a session has several user messages, the first question is graded against the last agent reply.

### What it does

1. If given a session reference, calls `fetch_qa_session`, which returns only the user's question and the agent's final answer. Search results and tool calls from that session are filtered out before anything reaches the model.
2. Converts every attached PDF to markdown (`document_conversion`) and reads it in full (`artifact_read`).
3. Opens the figures that bear on the question or that the answer refers to (`image_read`) and reads values off them directly.
4. Writes the key points a correct answer must contain, each tied to a place in the documents, and classifies every claim in the answer as supported, contradicted or not found.
5. Returns one score.

### Score

| Score | Meaning |
|---|---|
| 3 | Every main and secondary key point is present and correct, and no claim is contradicted or absent from the documents |
| 2 | Every main point is correct and nothing material is wrong, but a secondary point is missing or wrong, a main point is less precise than the documents, or there are minor claims the documents do not make |
| 1 | At least one main point is correct, but another main point is missing or contradicted, or the answer contains a material claim that the documents contradict or do not make |
| 0 | The answer does not address the question, gives no answer, says it could not find the information, or gets no main point right |

The one score covers accuracy, groundedness and relevance, and the weakest of the three decides. The full rubric and rules are in [prompt.md](prompt.md).

### Output

A structured verdict ([output_schema.json](output_schema.json)). The score is the last field, decided from the lists before it.

| Field | Content |
|---|---|
| `status` | `scored` in the normal case. `judge_error` when the judge agent could not complete: a PDF failed to convert or could not be read, a needed figure could not be opened, the Q&A session could not be fetched, or the input was incomplete. `ground_truth_lacks_answer` when the attached documents do not contain the answer |
| `key_points` | What a correct answer must contain, marked MAIN or SECONDARY, each with its source |
| `supported` | Answer claims confirmed by the documents |
| `unsupported_or_wrong` | Answer claims that are CONTRADICTED or NOT FOUND, marked MATERIAL or MINOR |
| `missing` | Key points absent from the answer |
| `figures_viewed` | Figures the judge agent opened |
| `reason` | Two to four sentences explaining the score |
| `score` | 0 to 3 |

Only a verdict with status `scored` is a judgement of the answer. With the other two statuses no judgement was made, and the scoring script leaves the score empty.

## The scoring script

Set the key in the environment first (`read -rs VECTARA_API_KEY && export VECTARA_API_KEY` keeps it out of shell history), then:

```bash
python judge_answers.py --input answers.csv --pdf-dir /path/to/ground_truth_pdfs --out judged.csv
```

Input columns: `id`, `ground_truth_files` (names or paths, several separated by `;`), and either `qa_agent` + `qa_session` or `question` + `answer`.

Options: `--only id1,id2`, `--force` (redo rows already scored), `--skip-hhem`, `--skip-judge`, `--price-in` and `--price-out` (USD per million tokens of the recipe's model; the cost column stays empty unless both are given). Re-running with the same `--out` only redoes rows that are missing or have an error.

It needs `requests` and `pymupdf` (`pip install requests pymupdf`).

Reading the output:

- Leave out any row whose `error` column is not empty. For example, when a session cannot be fetched the row has no score and the reason is in `error`.
- When a session produced no answer at all, the script records score 0 itself, with the reason "empty answer (not sent to the judge)", without calling the judge agent.
- HHEM compares the answer with the text of the ground-truth PDFs. An answer read correctly off a figure can score low on HHEM, so for figure questions give `judge_score` more weight.
- `qa_total_*` tokens are added up over all traces of the session. `qa_context_tokens` is only the most recent model call and shows how full the window got.

## Deploying the judge agent to an account

The target account needs: the model `claude-sonnet-4-6`, the built-in tools `artifact_read` and `image_read`, the document conversion tool `tol_vectara_document_conversion_20260220`, the built-in `web_get` tool (`tol_vectara_web_get`), the custom fetch tool created in step 1, and one agent secret.

Run these from the `judge/` folder, with both variables set:

```bash
export VECTARA_BASE_URL=https://api.vectara.dev/v2
```

1. Create the fetch tool and note the id it returns:

```bash
curl -X POST "$VECTARA_BASE_URL/tools" -H "x-api-key: $VECTARA_API_KEY" -H "Content-Type: application/json" --data @tools/fetch_qa_session.tool.json
```

2. In `agent.json`, under `tool_configurations.fetch_qa_session`, set `tool_id` to that id (the committed value is the id on the staging account) and set `argument_override.api_base_url` to the same base URL you are deploying to. The fetch tool sends the stored key to that URL, so it must be the environment the key belongs to. Then create the agent:

```bash
curl -X POST "$VECTARA_BASE_URL/agents" -H "x-api-key: $VECTARA_API_KEY" -H "Content-Type: application/json" --data @agent.json
```

3. Store the key the fetch tool should use as an agent secret named `vectara_api_key`. The platform returns it masked on read, and the tool references it by name, so it does not appear in the agent definition or in the model's input:

```bash
curl -X PATCH "$VECTARA_BASE_URL/agents/qa_answer_judge/secrets" -H "x-api-key: $VECTARA_API_KEY" -H "Content-Type: application/json" -d "{\"secrets\": {\"vectara_api_key\": \"$JUDGE_FETCH_KEY\"}}"
```

Prefer a dedicated key in `JUDGE_FETCH_KEY` that can only read agent sessions, not your administrative key: the judge agent will use it to read the question and final answer of whatever session it is pointed at. To rotate it, repeat the command with the new key.

`tools/fetch_qa_session.py` is the readable copy of the tool's code. The same code is inlined in `tools/fetch_qa_session.tool.json`; keep the two in sync when editing. `scripts/export_agent.py` refreshes only `agent.json` and `prompt.md`; `output_schema.json` and the files under `tools/` are maintained by hand.
