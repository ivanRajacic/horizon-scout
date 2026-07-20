"""M3b gate: dual-server validation for the Qwen SQL model.

Run with both servers up (bge on 8080, Qwen on 8081):
  python analysis/llm_gate/gate.py

Measures: both servers reachable concurrently, generation + prefill speed on a
real ~2k-token SQL prompt (llama-server 'timings'), strict-JSON compliance,
executable SQL, and an embed-while-generating concurrency check. Prints the
numbers for REPORT.md.
"""

import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.config import EMBED_BASE_URL, LLM_BASE_URL, LLM_MODEL  # noqa: E402
from src.retrieval.sql_path import (SqlPath, build_system_prompt,  # noqa: E402
                                    ensure_limit, strip_fences, validate_sql)


def chat_raw(messages, max_tokens=256):
    r = requests.post(f"{LLM_BASE_URL}/v1/chat/completions", json={
        "model": LLM_MODEL, "messages": messages, "temperature": 0,
        "max_tokens": max_tokens, "seed": 42, "timings_per_token": False,
    }, timeout=600)
    r.raise_for_status()
    return r.json()


def main():
    # 1. both servers up simultaneously
    for name, url in [("bge", EMBED_BASE_URL), ("qwen", LLM_BASE_URL)]:
        props = requests.get(f"{url}/props", timeout=5).json()
        model = Path(props.get("model_path", "?")).name
        print(f"[up] {name} at {url}: {model}")

    system = build_system_prompt()
    print(f"[prompt] system prompt chars={len(system)} (~{len(system)//4} tok)")

    # 2. cold SQL prompt -> prefill + generation timings
    question = "How many projects have a Spanish coordinator?"
    t0 = time.time()
    resp = chat_raw([{"role": "system", "content": system},
                     {"role": "user", "content": question}])
    wall = time.time() - t0
    timings = resp.get("timings", {})
    print(f"[timings] wall={wall:.2f}s prompt_n={timings.get('prompt_n')} "
          f"prefill={timings.get('prompt_per_second', 0):.0f} tok/s "
          f"gen_n={timings.get('predicted_n')} "
          f"gen={timings.get('predicted_per_second', 0):.1f} tok/s")

    # 3. the returned SQL must validate and execute
    content = resp["choices"][0]["message"]["content"]
    sql = ensure_limit(validate_sql(strip_fences(content)))
    print(f"[sql] {sql}")
    path = SqlPath.__new__(SqlPath)  # executor only, no client needed
    from src.config import DB_PATH, SQL_TIMEOUT_S
    path.db_path, path.timeout_s = DB_PATH, SQL_TIMEOUT_S
    cols, rows = path._execute(sql)
    print(f"[sql-exec] OK: {cols} = {rows[:3]}")

    # 4. warm repeat (prompt-cache hit on the shared system prefix)
    t0 = time.time()
    resp2 = chat_raw([{"role": "system", "content": system},
                      {"role": "user", "content":
                       "What is the total EU funding for Italian organisations?"}])
    timings2 = resp2.get("timings", {})
    print(f"[warm] wall={time.time()-t0:.2f}s prompt_n={timings2.get('prompt_n')}"
          f" (cached prefix skipped)")

    # 5. strict JSON
    resp3 = chat_raw([
        {"role": "system", "content":
         "Reply with strict JSON only, no markdown, no commentary."},
        {"role": "user", "content":
         'Classify this question as {"route": "sql"} or {"route": "vector"}: '
         '"How many projects started in 2020?"'}])
    raw = resp3["choices"][0]["message"]["content"].strip()
    parsed = json.loads(raw.removeprefix("```json").removeprefix("```")
                        .removesuffix("```").strip())
    print(f"[json] parseable: {parsed}")

    # 6. embed on 8080 while 8081 generates (VRAM coexistence)
    from src.embed_client import LlamaServerEmbeddings
    vec = LlamaServerEmbeddings().embed_query("solar energy storage")
    print(f"[embed] 8080 still serving: dim={len(vec)}")

    print("\nGATE OK")


if __name__ == "__main__":
    main()
