# Recipes

A recipe is one way of composing the model's context window for question answering: which corpus is read (and so how the documents were chunked and how figures and tables were indexed), which parts are retrieved, how many, by what kind of search, in what form they enter the context, and how the conversation is kept inside the window. Each recipe is a Q&A agent. All recipes are judged the same way (see [../judge/README.md](../judge/README.md)), so their scores and token usage can be compared.

| Recipe | Corpus | Parts retrieved | Search | Reranker | Form of parts in context | Model |
|---|---|---|---|---|---|---|
| [chartbook_v2_agent_chunk3000](chartbook_v2_agent_chunk3000/README.md) | `chartbook_v2_chunk3000` (about 3000-character chunks) | top 10 | semantic only | none | full text of each part; figures opened on demand | onprem-gemma4 |

More recipes will be added here. To add one, see "Adding a recipe" in the [top-level README](../README.md).
