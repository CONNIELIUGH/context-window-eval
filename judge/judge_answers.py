#!/usr/bin/env python3
"""
Score Q&A-agent answers against ground-truth PDF documents.

For every row of the input CSV this script produces:
  judge_score   0-3, ONE score covering accuracy, groundedness and relevance, given by the judge agent
                (qa_answer_judge, an advanced LLM) that reads the attached ground-truth PDFs itself. The judge never
                sees what the Q&A agent retrieved: only the question, the answer and the ground-truth PDFs.
  hhem_score    0-1 from Vectara's HHEM (POST /v2/evaluate_factual_consistency, hhem_v2.3): how consistent the answer
                is with the TEXT of the ground-truth PDFs. Text only: a value that exists only inside a figure is
                invisible to HHEM, so read it together with judge_score for figure questions.
  token usage   of the Q&A session (when qa_agent + qa_session are given):
                  qa_total_input_tokens / qa_total_output_tokens  every model call of the session added up (what is billed)
                  qa_context_tokens / qa_context_window           size of the LAST model call = how full the context window got
  qa_cost_usd   total tokens x price, when --price-in / --price-out (USD per 1M tokens) are given

Setup:
    export VECTARA_API_KEY=...          # staging key; api.vectara.dev is the default (VECTARA_BASE_URL overrides)

Run:
    python judge_answers.py --input answers.csv --pdf-dir "/path/to/ground/truth/pdfs" --out judged.csv

Input CSV columns:
    id, ground_truth_files        required; file names or paths, several separated by ';'
    qa_agent, qa_session          the Q&A agent key and session id: the script reads the question and the final answer
                                  from that session, and its token usage
    question, answer              optional; give them instead of (or to override) the session lookup

Re-running with the same --out keeps rows already scored without error and only does the rest (use --force to redo).

Cumulative tokens of one Q&A agent over a time window (all sessions):
    python judge_answers.py --usage-agent chartbook_v2_agent_chunk3000 --usage-start 2026-10-01T00:00:00Z --usage-end 2026-10-01T06:00:00Z
"""
import argparse, csv, json, os, re, sys, time

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

BASE = os.environ.get("VECTARA_BASE_URL", "https://api.vectara.dev/v2")
API_KEY = os.environ.get("VECTARA_API_KEY", "")
JUDGE_AGENT = "qa_answer_judge"
HDR = {"x-api-key": API_KEY}
JSON_HDR = {**HDR, "Content-Type": "application/json"}
OUT_FIELDS = ["id", "question", "answer", "judge_score", "judge_status", "hhem_score", "judge_reason", "missing", "unsupported_or_wrong",
              "supported", "key_points", "figures_viewed", "judge_session", "judge_seconds", "qa_agent", "qa_session",
              "qa_total_input_tokens", "qa_total_output_tokens", "qa_model_seconds", "qa_context_tokens", "qa_context_window",
              "qa_cost_usd", "error"]


def check(r, what):
    if r.status_code >= 400:
        raise RuntimeError(f"{what}: HTTP {r.status_code} {r.text[:400]}")
    return r


def paged(url, key, params=None):
    out, page = [], None
    while True:
        p = {"limit": 100, **(params or {}), **({"page_key": page} if page else {})}
        body = check(requests.get(url, headers=HDR, params=p, timeout=60), f"GET {url}").json()
        out += body.get(key, [])
        page = (body.get("metadata") or {}).get("page_key")
        if not page:
            return out


# ------------------------------------------------------------------ ground-truth files
def resolve_files(spec, pdf_dir):
    paths = []
    for name in [s.strip() for s in (spec or "").split(";") if s.strip()]:
        p = name if os.path.isabs(name) else os.path.join(pdf_dir or ".", name)
        if not os.path.isfile(p) and not p.lower().endswith(".pdf") and os.path.isfile(p + ".pdf"):
            p += ".pdf"
        if not os.path.isfile(p):
            raise FileNotFoundError(f"ground-truth file not found: {p}")
        paths.append(p)
    if not paths:
        raise ValueError("no ground-truth file listed")
    return paths


def pdf_page_texts(path):
    """One text per page, for HHEM (PyMuPDF)."""
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz
    return [t for t in (page.get_text().strip() for page in fitz.open(path)) if t]


# ------------------------------------------------------------------ the Q&A session: question, answer, tokens
def fetch_qa(agent, session):
    """Question = first user message, answer = last non-empty agent reply of the session."""
    events = sorted(paged(f"{BASE}/agents/{agent}/sessions/{session}/events", "events"), key=lambda e: e.get("created_at", ""))
    question = ""
    for e in events:
        if e.get("type") == "input_message":
            msgs = e.get("messages") or []
            question = " ".join(m.get("content", "") for m in msgs if isinstance(m, dict) and m.get("type") == "text").strip() or str(e.get("content") or "")
            break
    answer = ""
    for e in events:
        if e.get("type") == "agent_output" and str(e.get("content") or "").strip():
            answer = str(e["content"]).strip()
        elif e.get("type") == "structured_output":
            answer = json.dumps(e.get("content"), ensure_ascii=False)
    return question, answer


def session_usage(agent, session):
    out = {}
    traces = paged(f"{BASE}/agent_analytics/traces", "traces", {"agent_key": agent, "session_key": session})
    out["qa_total_input_tokens"] = sum(t.get("input_tokens") or 0 for t in traces)
    out["qa_total_output_tokens"] = sum(t.get("output_tokens") or 0 for t in traces)
    out["qa_model_seconds"] = round(sum(t.get("duration_ms") or 0 for t in traces) / 1000, 1)
    s = check(requests.get(f"{BASE}/agents/{agent}/sessions/{session}", headers=HDR, timeout=60), "GET session").json()
    u = s.get("session_context_usage") or {}
    out["qa_context_tokens"] = u.get("total_tokens")
    out["qa_context_window"] = u.get("model_context_window")
    if not traces:
        out["qa_total_input_tokens"] = out["qa_total_output_tokens"] = out["qa_model_seconds"] = ""
    return out


def cost(inp, outp, price_in, price_out):
    if not isinstance(inp, (int, float)) or not isinstance(outp, (int, float)) or price_in is None or price_out is None:
        return None
    return round(inp / 1e6 * price_in + outp / 1e6 * price_out, 6)


# ------------------------------------------------------------------ HHEM
def hhem_score(answer, source_texts):
    if not source_texts:
        raise ValueError("HHEM needs at least one non-empty source text")
    body = {"generated_text": answer, "source_texts": source_texts, "model_parameters": {"model_name": "hhem_v2.3"}}
    last = None
    for attempt in range(3):
        try:
            r = requests.post(f"{BASE}/evaluate_factual_consistency", headers=JSON_HDR, json=body, timeout=180)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code} {r.text[:200]}"
                time.sleep(int(r.headers.get("Retry-After", 5 * (attempt + 1))))
                continue
            return check(r, "HHEM").json()["score"]
        except (requests.Timeout, requests.ConnectionError) as e:
            last = f"{type(e).__name__}"
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"HHEM failed after retries: {last}")


# ------------------------------------------------------------------ judge agent
def _verdict_from_events(events):
    v = [e for e in events if e.get("type") == "structured_output"]
    if not v:
        return None
    c = v[-1].get("content")
    return json.loads(c) if isinstance(c, str) else c


def judge(question, answer, pdf_paths, tag, timeout=1700):
    safe = re.sub(r"[^0-9A-Za-z_-]+", "_", str(tag))[:20]      # the platform derives a <=50-char session key from the name
    r = check(requests.post(f"{BASE}/agents/{JUDGE_AGENT}/sessions", headers=JSON_HDR,
                            json={"name": f"judge-{safe}-{int(time.time() * 1000)}"}, timeout=60), "create judge session")
    session = r.json()["key"]
    message = f"QUESTION:\n{question.strip()}\n\nANSWER:\n{answer.strip()}"
    files = [("files", (os.path.basename(p), open(p, "rb").read(), "application/pdf")) for p in pdf_paths]
    files += [("messages", (None, json.dumps([{"type": "text", "content": message}]), "application/json")),
              ("stream_response", (None, "false"))]
    t0 = time.time()
    verdict = None
    try:
        resp = requests.post(f"{BASE}/agents/{JUDGE_AGENT}/sessions/{session}/events", headers=HDR, files=files, timeout=timeout)
        if resp.status_code < 400:
            verdict = _verdict_from_events(resp.json().get("events", []))
        elif resp.status_code < 500:
            raise RuntimeError(f"judge turn rejected: HTTP {resp.status_code} {resp.text[:300]} (session {session})")
    except (requests.Timeout, requests.ConnectionError):
        pass
    if verdict is None:                                   # timeout / 5xx / no verdict in the body: the turn may still be running
        deadline = time.time() + 900
        while time.time() < deadline:
            s = requests.get(f"{BASE}/agents/{JUDGE_AGENT}/sessions/{session}", headers=HDR, timeout=60)
            if s.status_code == 200 and s.json().get("status") == "stopped":
                break
            time.sleep(15)
        events = sorted(paged(f"{BASE}/agents/{JUDGE_AGENT}/sessions/{session}/events", "events"), key=lambda e: e.get("created_at", ""))
        verdict = _verdict_from_events(events)
        if verdict is None:
            last = [e for e in events if e.get("type") in ("agent_output", "context_limit_exceeded", "error")]
            raise RuntimeError(f"judge returned no verdict (session {session}): {json.dumps(last[-1])[:300] if last else 'no terminal event'}")
    return verdict, session, round(time.time() - t0, 1)


# ------------------------------------------------------------------ agent-level usage
def agent_usage(agent, start, end):
    out = {}
    for name in ("agent.trace.input_tokens", "agent.trace.output_tokens", "agent.trace.cache_read_tokens", "agent.trace.count"):
        r = requests.get(f"{BASE}/metrics/{name}", headers=HDR, timeout=60,
                         params={"dimensions[agent_keys]": agent, "start": start, "end": end, "max_bins": 1})
        if r.status_code != 200:
            out[name] = f"HTTP {r.status_code}: {r.text[:200]}"
            continue
        body = r.json()
        if body.get("type") not in ("counter", "gauge"):
            out[name] = f"unsupported metric type {body.get('type')}"
            continue
        pts = body.get("points") or []
        out[name] = sum(p.get("value") or 0 for p in pts)
        if pts:
            out["window_actual"] = f"from {pts[0].get('bin_start')} for {body.get('bin_size_seconds')} s (the platform rounds the window outward)"
    return out


# ------------------------------------------------------------------ main
def write_out(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input")
    ap.add_argument("--out", default="judged.csv")
    ap.add_argument("--pdf-dir", default=None, help="folder holding the ground-truth PDFs (for relative names in the CSV)")
    ap.add_argument("--only", default=None, help="comma-separated ids")
    ap.add_argument("--force", action="store_true", help="redo rows that are already scored in --out")
    ap.add_argument("--skip-hhem", action="store_true")
    ap.add_argument("--skip-judge", action="store_true")
    ap.add_argument("--price-in", type=float, default=None, help="USD per 1M input tokens of the Q&A model")
    ap.add_argument("--price-out", type=float, default=None, help="USD per 1M output tokens of the Q&A model")
    ap.add_argument("--usage-agent", default=None)
    ap.add_argument("--usage-start", default=None)
    ap.add_argument("--usage-end", default=None)
    args = ap.parse_args()
    if not API_KEY:
        sys.exit("Set VECTARA_API_KEY first.")

    if args.usage_agent:
        if not (args.usage_start and args.usage_end):
            sys.exit("--usage-start and --usage-end (ISO 8601, e.g. 2026-10-01T00:00:00Z) are required with --usage-agent")
        u = agent_usage(args.usage_agent, args.usage_start, args.usage_end)
        print(json.dumps({"agent": args.usage_agent, "requested_start": args.usage_start, "requested_end": args.usage_end, **u}, indent=2))
        c = cost(u.get("agent.trace.input_tokens"), u.get("agent.trace.output_tokens"), args.price_in, args.price_out)
        if c is not None:
            print("cost_usd:", c)
        return

    if not args.input:
        sys.exit("--input is required")
    if os.path.abspath(args.input) == os.path.abspath(args.out):
        sys.exit("--out must be a different file from --input")
    with open(args.input, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = set(reader.fieldnames or [])
        rows = [{k: (v or "").strip() for k, v in r.items() if k} for r in reader]
    if not {"id", "ground_truth_files"} <= fields:
        sys.exit("input CSV needs at least the columns: id, ground_truth_files (plus qa_agent + qa_session, or question + answer)")
    if args.only:
        keep = {s.strip() for s in args.only.split(",") if s.strip()}
        missing_ids = keep - {r["id"] for r in rows}
        rows = [r for r in rows if r["id"] in keep]
        if missing_ids:
            print("ids not found in input:", sorted(missing_ids))
    if not rows:
        sys.exit("no rows to process")

    prev = {}
    if os.path.exists(args.out):
        with open(args.out, newline="", encoding="utf-8") as fh:
            prev = {r["id"]: r for r in csv.DictReader(fh)}
    done = dict(prev)

    for n, row in enumerate(rows, 1):
        rid = row["id"]
        old = prev.get(rid)
        if old and not args.force and not old.get("error") and (old.get("judge_score") != "" or args.skip_judge):
            print(f"[{n}/{len(rows)}] {rid}: already scored (judge={old.get('judge_score')}), skipped")
            continue
        res = {k: "" for k in OUT_FIELDS}
        res.update({"id": rid, "qa_agent": row.get("qa_agent", ""), "qa_session": row.get("qa_session", "")})
        errors = []
        question, answer = row.get("question", ""), row.get("answer", "")
        has_session = bool(row.get("qa_agent") and row.get("qa_session"))
        lookup_failed = False
        if has_session and (not question or "answer" not in fields or not answer):
            try:
                q2, a2 = fetch_qa(row["qa_agent"], row["qa_session"])
                question = question or q2
                answer = answer or a2
            except Exception as e:
                lookup_failed = True
                errors.append(f"session lookup: {e}")
        res["question"], res["answer"] = question, answer
        try:
            pdfs = resolve_files(row.get("ground_truth_files", ""), args.pdf_dir)
        except Exception as e:
            pdfs = []
            errors.append(str(e))
        if not question:
            errors.append("no question (give question, or qa_agent + qa_session)")

        if lookup_failed:
            pass                                            # the answer is unknown, not empty: leave the row unscored (see the error column)
        elif not answer.strip():
            # the session really produced no answer: the script scores it 0 itself, without calling the judge
            res.update({"judge_score": 0, "judge_status": "scored", "judge_reason": "empty answer (not sent to the judge)"})
        elif pdfs and question:
            if not args.skip_hhem:
                try:
                    res["hhem_score"] = round(hhem_score(answer, [t for p in pdfs for t in pdf_page_texts(p)]), 4)
                except Exception as e:
                    errors.append(f"hhem: {e}")
            if not args.skip_judge:
                try:
                    v, session, seconds = judge(question, answer, pdfs, rid)
                    status = v.get("status", "scored")
                    res.update({"judge_status": status, "judge_reason": v.get("reason", ""), "judge_session": session, "judge_seconds": seconds,
                                "missing": " | ".join(v.get("missing", [])), "unsupported_or_wrong": " | ".join(v.get("unsupported_or_wrong", [])),
                                "supported": " | ".join(v.get("supported", [])), "key_points": " | ".join(v.get("key_points", [])),
                                "figures_viewed": ";".join(v.get("figures_viewed", []))})
                    if status == "scored":
                        res["judge_score"] = v.get("score")
                        if v.get("score") == 3 and (v.get("missing") or v.get("unsupported_or_wrong")):
                            errors.append("inconsistent verdict: score 3 with non-empty missing/unsupported lists (review by hand)")
                    else:                                   # not a judgement of the answer: keep it out of the averages
                        errors.append(f"judge status {status}: {str(v.get('reason', ''))[:300]} (session {session})")
                except Exception as e:
                    errors.append(f"judge: {e}")
        if has_session:
            try:
                u = session_usage(row["qa_agent"], row["qa_session"])
                res.update(u)
                c = cost(u["qa_total_input_tokens"], u["qa_total_output_tokens"], args.price_in, args.price_out)
                res["qa_cost_usd"] = "" if c is None else c
            except Exception as e:
                errors.append(f"usage: {e}")
        res["error"] = "; ".join(errors)
        done[rid] = res
        print(f"[{n}/{len(rows)}] {rid}: judge={res['judge_score']} status={res['judge_status']} hhem={res['hhem_score']} "
              f"tokens_in/out={res['qa_total_input_tokens']}/{res['qa_total_output_tokens']} {('ERR ' + res['error']) if errors else ''}")
        write_out(args.out, list(done.values()))            # written after every row: a crash or a re-run loses nothing
    write_out(args.out, list(done.values()))
    print(f"wrote {args.out} ({len(done)} rows)")


if __name__ == "__main__":
    main()
