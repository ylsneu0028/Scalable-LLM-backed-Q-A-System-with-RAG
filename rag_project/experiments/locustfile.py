"""
Locust load test for POST /query (full RAG: embed + vector + LLM).

  cd rag_project
  pip install -r experiments/requirements-bench.txt

  # Web UI (set host in the UI or use --host)
  locust -f experiments/locustfile.py --host http://127.0.0.1:8000

  # Headless: 50 users, spawn 5/s, run 5 minutes, HTML report
  locust -f experiments/locustfile.py --host http://127.0.0.1:8000 \\
    --headless -u 50 -r 5 -t 5m --html results/locust_report.html

Environment:
  LOCUST_QUESTION      if set, use this text (each request still gets a unique rid=)
  LOCUST_MIN_WAIT      default 0
  LOCUST_MAX_WAIT      default 0 (saturate workers; increase for more realistic pacing)
  LOCUST_TIMINGS_FILE  if set, append each /query response's timings_ms to this JSONL file
                       (one line per request: {ts, embed_ms, retrieve_ms, llm_ms, total_ms, status})
"""

from __future__ import annotations

import json
import os
import random
import threading
import time

from locust import HttpUser, between, events, task

_TIMINGS_FILE = os.environ.get("LOCUST_TIMINGS_FILE", "").strip()
_timings_lock = threading.Lock()
_timings_fh = None


@events.init.add_listener
def _open_timings(environment, **_):
    global _timings_fh
    if _TIMINGS_FILE:
        parent = os.path.dirname(_TIMINGS_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _timings_fh = open(_TIMINGS_FILE, "a", buffering=1)


@events.quitting.add_listener
def _close_timings(environment, **_):
    global _timings_fh
    if _timings_fh is not None:
        _timings_fh.close()
        _timings_fh = None


def _record_timings(payload: dict) -> None:
    if _timings_fh is None:
        return
    with _timings_lock:
        _timings_fh.write(json.dumps(payload) + "\n")

BASE_QUESTIONS = [
    "What is the on-call policy for P1 incidents?",
    "How long do we have to acknowledge a P1?",
    "When does the on-call rotation reset?",
    "Who do we escalate database issues to?",
    "What is the release process for production?",
    "Where is rollback documented?",
]


def next_question() -> str:
    base = random.choice(BASE_QUESTIONS)
    custom = os.environ.get("LOCUST_QUESTION", "").strip()
    rid = time.time_ns()
    if custom:
        return f"{custom} (rid={rid})"
    return f"{base} (rid={rid})"


_min = float(os.environ.get("LOCUST_MIN_WAIT", "0"))
_max = float(os.environ.get("LOCUST_MAX_WAIT", "0"))
if _max < _min:
    _max = _min


class RAGQueryUser(HttpUser):
    wait_time = between(_min, _max)

    @task
    def query(self) -> None:
        q = next_question()
        with self.client.post(
            "/query",
            json={"question": q},
            catch_response=True,
            name="/query",
            timeout=600.0,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"HTTP {resp.status_code}: {resp.text[:200]!r}")
                return
            try:
                data = resp.json()
            except Exception:
                resp.failure("response is not JSON")
                return
            timings = data.get("timings_ms") or {}
            if timings:
                _record_timings({
                    "ts": time.time(),
                    "status": resp.status_code,
                    **{k: timings.get(k) for k in ("embed_ms", "retrieve_ms", "llm_ms", "total_ms")},
                })
            if not (data.get("answer") or "").strip():
                resp.failure("empty answer")
            else:
                resp.success()
