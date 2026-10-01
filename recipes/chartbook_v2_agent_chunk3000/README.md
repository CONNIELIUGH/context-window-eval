# Recipe: `chartbook_v2_agent_chunk3000`

One combination of settings for composing the context window. It is the first recipe in this repository; later recipes change one or more of the settings below.

## How this recipe composes the context window

| Setting | Value |
|---|---|
| Model | `onprem-gemma4` |
| Corpus | `chartbook_v2_chunk3000`: body text in fixed chunks of about 3000 characters, figures embedded as the actual image with a short caption, tables as structured table parts, encoder `boomerang-v2` |
| Search tool | `book_search` (corpus search) |
| Parts retrieved per search | 10 |
| Search type | semantic only (`lexical_interpolation` 0.0, no keyword component) |
| Reranker | none |
| Platform-generated summary | off: the agent's model reads the retrieved parts and writes the answer itself |
| Form of parts in context | the full text of every retrieved part, no summarization |
| Figures | not placed in the context by the search. The agent fetches a relevant figure with `get_figure_image` and views it with `image_read` (detail `auto`) before answering |
| Number of searches | not limited by the prompt |
| Compaction | on at 80 percent of the window, keeping the most recent input, tool outputs included |
| Large tool outputs | offloading on, in `truncate` mode, for outputs above 25 percent of the window |

The exact definition is in [agent.json](agent.json) and the instructions in [prompt.md](prompt.md). The `chunk3000` in the name refers to the corpus this recipe reads.

## What it depends on

- Corpus `chartbook_v2_chunk3000` on the staging account.
- The model `onprem-gemma4` registered on that account.
- The tool `tol_vectara_get_document_image` and the built-in `image_read` tool.

## Settings later recipes are expected to vary

- The corpus (chunk size, how figures are captioned)
- Parts retrieved (for example 5 instead of 10)
- Semantic versus hybrid search (`lexical_interpolation` above 0)
- Passing parts in full versus summarizing them before they enter the context
- Limits on the number of searches per question
- Compaction and offloading thresholds
