"""Prometheus metrics without extra dependencies.

The API runs several worker processes, so each process keeps its counters in memory and writes a snapshot file at
most once per second; a scrape merges the snapshots of all live processes. Business metrics (job queue, agent runs,
documents) are read from PostgreSQL at scrape time, so they are exact across processes and restarts.
"""
import json
import math
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import get_settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

settings = get_settings()
DEFAULT_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(names: tuple[str, ...], values: tuple[str, ...], extra: str = "") -> str:
    parts = [f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True)]
    if extra:
        parts.append(extra)
    return "{" + ",".join(parts) + "}" if parts else ""


def _num(value: float) -> str:
    if value == math.inf:
        return "+Inf"
    return str(int(value)) if float(value).is_integer() else repr(float(value))


@dataclass
class Counter:
    name: str
    help: str
    labelnames: tuple[str, ...] = ()
    values: dict[tuple[str, ...], float] = field(default_factory=dict)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = tuple(str(labels.get(n, "")) for n in self.labelnames)
        with REGISTRY.lock:
            self.values[key] = self.values.get(key, 0.0) + amount
        REGISTRY.changed()


@dataclass
class Histogram:
    name: str
    help: str
    labelnames: tuple[str, ...] = ()
    buckets: tuple[float, ...] = DEFAULT_BUCKETS
    values: dict[tuple[str, ...], dict[str, Any]] = field(default_factory=dict)

    def observe(self, value: float, **labels: str) -> None:
        key = tuple(str(labels.get(n, "")) for n in self.labelnames)
        with REGISTRY.lock:
            entry = self.values.setdefault(key, {"buckets": [0] * len(self.buckets), "sum": 0.0, "count": 0})
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    entry["buckets"][i] += 1
            entry["sum"] += value
            entry["count"] += 1
        REGISTRY.changed()


class Registry:
    def __init__(self) -> None:
        self.metrics: dict[str, Counter | Histogram] = {}
        self.lock = threading.Lock()
        self._last_write = 0.0

    def counter(self, name: str, help_text: str, labelnames: tuple[str, ...] = ()) -> Counter:
        metric = Counter(name, help_text, labelnames)
        self.metrics[name] = metric
        return metric

    def histogram(self, name: str, help_text: str, labelnames: tuple[str, ...] = (),
                  buckets: tuple[float, ...] = DEFAULT_BUCKETS) -> Histogram:
        metric = Histogram(name, help_text, labelnames, buckets)
        self.metrics[name] = metric
        return metric

    # --- cross-process snapshots ----------------------------------------------------------------------------------------
    @property
    def directory(self) -> Path:
        return Path(settings.metrics_dir)

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            out: dict[str, Any] = {}
            for name, m in self.metrics.items():
                if isinstance(m, Counter):
                    out[name] = [[list(k), v] for k, v in m.values.items()]
                else:
                    out[name] = [[list(k), {"buckets": list(v["buckets"]), "sum": v["sum"], "count": v["count"]}]
                                 for k, v in m.values.items()]
            return out

    def changed(self) -> None:
        now = time.monotonic()
        if now - self._last_write >= 1.0:
            self.write()

    def write(self) -> None:
        self._last_write = time.monotonic()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{os.getpid()}.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.snapshot()))
            tmp.replace(path)
        except OSError:
            pass  # metrics must never break a request

    def merged(self) -> dict[str, dict[tuple[str, ...], Any]]:
        """Own live values plus the snapshots of other live processes."""
        merged: dict[str, dict[tuple[str, ...], Any]] = {name: {} for name in self.metrics}
        snapshots = [self.snapshot()]
        try:
            for file in self.directory.glob("*.json"):
                pid = int(file.stem) if file.stem.isdigit() else -1
                if pid == os.getpid() or not _alive(pid):
                    continue
                try:
                    snapshots.append(json.loads(file.read_text()))
                except (OSError, ValueError):
                    continue
        except OSError:
            pass
        for snap in snapshots:
            for name, rows in snap.items():
                metric = self.metrics.get(name)
                if metric is None:
                    continue
                target = merged[name]
                for key_list, value in rows:
                    key = tuple(key_list)
                    if isinstance(metric, Counter):
                        target[key] = target.get(key, 0.0) + value
                    else:
                        cur = target.setdefault(key, {"buckets": [0] * len(metric.buckets), "sum": 0.0, "count": 0})
                        cur["buckets"] = [a + b for a, b in zip(cur["buckets"], value["buckets"], strict=True)]
                        cur["sum"] += value["sum"]
                        cur["count"] += value["count"]
        return merged

    def render(self) -> str:
        merged = self.merged()
        lines: list[str] = []
        for name, metric in self.metrics.items():
            kind = "counter" if isinstance(metric, Counter) else "histogram"
            lines += [f"# HELP {name} {metric.help}", f"# TYPE {name} {kind}"]
            for key in sorted(merged[name]):
                value = merged[name][key]
                if isinstance(metric, Counter):
                    lines.append(f"{name}{_labels(metric.labelnames, key)} {_num(value)}")
                    continue
                for bound, count in zip(metric.buckets, value["buckets"], strict=True):
                    lines.append(f"{name}_bucket{_labels(metric.labelnames, key, f'le={chr(34)}{_num(bound)}{chr(34)}')} {count}")
                lines.append(f"{name}_bucket{_labels(metric.labelnames, key, 'le=' + chr(34) + '+Inf' + chr(34))} {value['count']}")
                lines.append(f"{name}_sum{_labels(metric.labelnames, key)} {_num(round(value['sum'], 6))}")
                lines.append(f"{name}_count{_labels(metric.labelnames, key)} {value['count']}")
        return "\n".join(lines) + "\n"


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


REGISTRY = Registry()
HTTP_REQUESTS = REGISTRY.counter("copilot_http_requests_total", "HTTP requests handled by the API.", ("method", "route", "status"))
HTTP_DURATION = REGISTRY.histogram("copilot_http_request_duration_seconds", "API request latency.", ("method", "route"))
AGENT_ANSWERS = REGISTRY.counter("copilot_agent_answers_total", "Agent answers by mode and outcome.", ("mode", "status"))
AGENT_DURATION = REGISTRY.histogram("copilot_agent_answer_duration_seconds", "Time to produce an agent answer.", ("mode",),
                                    (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 80))
AGENT_TOOL_CALLS = REGISTRY.counter("copilot_agent_tool_calls_total", "Agent tool calls by tool and outcome.", ("tool", "status"))
AGENT_SAFETY = REGISTRY.counter("copilot_agent_safety_events_total", "Agent safety events (rejected model output, refusals, flags).", ("event",))
LLM_TOKENS = REGISTRY.counter("copilot_llm_tokens_total", "Model tokens used by the agent.", ("provider", "direction"))


# --- Database-derived gauges (exact across processes) ------------------------------------------------------------------------
async def database_gauges(db: "AsyncSession") -> str:
    """Aggregates only, never per-organization. The caller has put the session in system context."""
    from sqlalchemy import text

    lines: list[str] = []

    def gauge(name: str, help_text: str, rows: list[tuple[dict[str, str], float]]) -> None:
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} gauge"])
        for labels, value in rows:
            names = tuple(sorted(labels))
            lines.append(f"{name}{_labels(names, tuple(labels[n] for n in names))} {_num(float(value))}")

    jobs = (await db.execute(text("SELECT kind, status, count(*) FROM jobs GROUP BY kind, status"))).all()
    gauge("copilot_jobs", "Jobs in the queue by kind and status.", [({"kind": k, "status": s}, n) for k, s, n in jobs])
    oldest = await db.scalar(text("SELECT coalesce(extract(epoch FROM now() - min(run_at)), 0) FROM jobs "
                                  "WHERE status = 'queued' AND run_at <= now()"))
    gauge("copilot_jobs_oldest_queued_seconds", "Age of the oldest job that is due and not yet started.", [({}, float(oldest or 0))])
    docs = (await db.execute(text("SELECT status, count(*) FROM documents GROUP BY status"))).all()
    gauge("copilot_documents", "Documents by processing status.", [({"status": s}, n) for s, n in docs])
    runs = (await db.execute(text("SELECT mode, status, count(*) FROM agent_runs WHERE created_at > now() - interval '24 hours' "
                                  "GROUP BY mode, status"))).all()
    gauge("copilot_agent_runs_24h", "Agent runs in the last 24 hours by mode and outcome.",
          [({"mode": m, "status": s}, n) for m, s, n in runs])
    feedback = (await db.execute(text("SELECT feedback, count(*) FROM agent_messages WHERE feedback IS NOT NULL "
                                      "AND created_at > now() - interval '7 days' GROUP BY feedback"))).all()
    gauge("copilot_agent_feedback_7d", "Agent answer ratings in the last 7 days.",
          [({"rating": "up" if f > 0 else "down"}, n) for f, n in feedback])
    chunks = await db.scalar(text("SELECT count(*) FROM knowledge_chunks"))
    gauge("copilot_knowledge_chunks", "Indexed document passages available to the agent.", [({}, float(chunks or 0))])
    return "\n".join(lines) + "\n"
