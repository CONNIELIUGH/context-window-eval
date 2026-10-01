#!/usr/bin/env python3
"""
Export an agent definition from the Vectara API into this repository's layout.

    export VECTARA_API_KEY=...                      # never commit a key
    python scripts/export_agent.py chartbook_v2_agent_chunk3000 recipes/chartbook_v2_agent_chunk3000
    python scripts/export_agent.py qa_answer_judge judge

Writes into the target folder:
    agent.json   the agent definition with server-managed fields removed, ready to POST to /v2/agents
    prompt.md    the instruction text of the first step, for reading and for diffs

Agent secrets are never part of the export (the API does not return them).
Staging (https://api.vectara.dev/v2) is the default; set VECTARA_BASE_URL for another environment.
"""
import json, os, re, sys

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

BASE = os.environ.get("VECTARA_BASE_URL", "https://api.vectara.dev/v2")
SERVER_FIELDS = ("created_at", "updated_at", "enabled", "first_step_name", "steps")   # 'steps' mirrors first_step on read


def literal_auth_values(node, path="tool_configurations"):
    """Paths of auth / header values that are plain strings. A credential must be a {"$ref": "agent.secrets..."} reference."""
    found = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}"
            if k in ("auth", "headers") and isinstance(v, dict):
                for kk, vv in v.items():
                    if kk in ("type", "header"):
                        continue
                    if isinstance(vv, str) and vv:
                        found.append(f"{p}.{kk}")
            found += literal_auth_values(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            found += literal_auth_values(v, f"{path}[{i}]")
    return found


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    key, out_dir = sys.argv[1], sys.argv[2]
    api_key = os.environ.get("VECTARA_API_KEY")
    if not api_key:
        sys.exit("Set VECTARA_API_KEY first.")
    r = requests.get(f"{BASE}/agents/{key}", headers={"x-api-key": api_key}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"GET agent {key}: HTTP {r.status_code} {r.text[:300]}")
    agent = {k: v for k, v in r.json().items() if k not in SERVER_FIELDS}
    text = json.dumps(agent)
    if api_key in text or re.search(r"zut_|zqt_|gh[opsu]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}", text):
        sys.exit("refusing to write: the definition appears to contain a credential")
    literal = literal_auth_values(agent.get("tool_configurations") or {})
    if literal:
        sys.exit("refusing to write: these auth/header values are literal strings, not references to agent secrets: " + ", ".join(literal))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "agent.json"), "w", encoding="utf-8") as fh:
        json.dump(agent, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    prompt = agent["first_step"]["instructions"][0]["template"]
    with open(os.path.join(out_dir, "prompt.md"), "w", encoding="utf-8") as fh:
        fh.write(prompt.rstrip("\n") + "\n")
    print(f"exported {key} -> {out_dir}/agent.json, {out_dir}/prompt.md ({len(prompt)} prompt characters, model {agent['model']['name']})")


if __name__ == "__main__":
    main()
