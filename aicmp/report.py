"""Compare eval runs: accuracy, main-model vs delegate tokens, and $ per sample.

    python -m aicmp.report logs/                 # every .eval log in the directory
    python -m aicmp.report logs/a.eval logs/b.eval

"Main" is the model passed with --model; any other model in the log's usage (e.g. the
Shunt delegate) is counted as delegate. Prices come from aicmp/model_costs.yaml.
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from inspect_ai.log import list_eval_logs, read_eval_log
from inspect_ai.model import ModelUsage

COSTS = yaml.safe_load((Path(__file__).parent / "model_costs.yaml").read_text())


def usage_cost(model: str, u: ModelUsage) -> float | None:
    c = COSTS.get(model)
    if c is None:
        return None
    return (
        u.input_tokens * c["input"]
        + u.output_tokens * c["output"]
        + (u.input_tokens_cache_write or 0) * c["input_cache_write"]
        + (u.input_tokens_cache_read or 0) * c["input_cache_read"]
    ) / 1e6


def all_tokens(u: ModelUsage) -> int:
    return (
        u.input_tokens
        + u.output_tokens
        + (u.input_tokens_cache_write or 0)
        + (u.input_tokens_cache_read or 0)
    )


@dataclass
class Row:
    label: str
    samples: int
    accuracy: float | None
    strict: float | None
    main_tokens: float
    main_uncached_in: float
    main_cache_read: float
    main_out: float
    delegate_tokens: float
    main_cost: float | None
    delegate_cost: float | None
    minutes: float | None
    bulk_reads: float | None = None
    bulk_read_failures: int = 0
    hook_blocks: float | None = None


def shunt_usage(log) -> tuple[float | None, int, float | None]:
    """Per-sample bulk_read calls, total failures, per-sample hook blocks (large_read only)."""
    metas = [
        sc.metadata
        for s in log.samples or []
        for sc in (s.scores or {}).values()
        if sc.metadata and "bulk_read_calls" in sc.metadata
    ]
    if not metas:
        return None, 0, None
    n = len(metas)
    return (
        sum(m["bulk_read_calls"] for m in metas) / n,
        sum(m["bulk_read_failures"] for m in metas),
        sum(m["hook_blocks"] for m in metas) / n,
    )


def summarize(path: str) -> Row:
    log = read_eval_log(path)
    main = log.eval.model
    n = max(log.results.completed_samples if log.results else 0, 1)
    main_u, del_tokens, main_cost, del_cost = ModelUsage(), 0, 0.0, 0.0
    for model, u in log.stats.model_usage.items():
        cost = usage_cost(model, u)
        if model == main:
            main_u, main_cost = u, cost
        else:
            del_tokens += all_tokens(u)
            del_cost = None if cost is None or del_cost is None else del_cost + cost
    # large_read reports "correct" and "strict"; other tasks have a single score.
    scores = {sc.name: sc.metrics["accuracy"].value
              for sc in (log.results.scores if log.results else [])
              if "accuracy" in sc.metrics}
    acc = scores.get("correct", next(iter(scores.values()), None))
    strict = scores.get("strict")
    args = " ".join(f"{k}={v}" for k, v in (log.eval.task_args or {}).items())
    minutes = None
    if log.stats.started_at and log.stats.completed_at:
        from datetime import datetime

        start = datetime.fromisoformat(log.stats.started_at)
        end = datetime.fromisoformat(log.stats.completed_at)
        minutes = (end - start).total_seconds() / 60
    per = lambda v: v / n if v is not None else None  # noqa: E731
    bulk_reads, failures, blocks = shunt_usage(log)
    return Row(
        label=f"{log.eval.task} {args} [{main}]" + ("" if log.status == "success" else f" ({log.status})"),
        samples=n,
        accuracy=acc,
        strict=strict,
        main_tokens=all_tokens(main_u) / n,
        main_uncached_in=main_u.input_tokens / n,
        main_cache_read=(main_u.input_tokens_cache_read or 0) / n,
        main_out=main_u.output_tokens / n,
        delegate_tokens=del_tokens / n,
        main_cost=per(main_cost),
        delegate_cost=per(del_cost),
        minutes=minutes,
        bulk_reads=bulk_reads,
        bulk_read_failures=failures,
        hook_blocks=blocks,
    )


def fmt_table(rows: list[Row]) -> str:
    money = lambda v: "n/a" if v is None else f"${v:.4f}"  # noqa: E731
    k = lambda v: f"{v / 1000:.1f}k"  # noqa: E731
    header = [
        "run", "n", "acc", "strict", "main tok", "  uncached in", "  cache read", "  out",
        "delegate tok", "main $", "delegate $", "total $", "min", "bulk_read", "hook blocks",
    ]
    lines = []
    for r in rows:
        total = None if r.main_cost is None or r.delegate_cost is None else r.main_cost + r.delegate_cost
        lines.append([
            r.label, str(r.samples), "-" if r.accuracy is None else f"{r.accuracy:.2f}",
            "-" if r.strict is None else f"{r.strict:.2f}",
            k(r.main_tokens), k(r.main_uncached_in), k(r.main_cache_read), k(r.main_out),
            k(r.delegate_tokens), money(r.main_cost), money(r.delegate_cost), money(total),
            "-" if r.minutes is None else f"{r.minutes:.1f}",
            "-" if r.bulk_reads is None else f"{r.bulk_reads:.1f}",
            "-" if r.hook_blocks is None else f"{r.hook_blocks:.1f}",
        ])
    widths = [max(len(x) for x in col) for col in zip(header, *lines)]
    out = ["  ".join(h.ljust(w) for h, w in zip(header, widths))]
    out += ["  ".join(c.ljust(w) for c, w in zip(line, widths)) for line in lines]
    notes = ["Token, $, bulk_read and hook-block columns are per sample (averaged over samples x epochs)."]
    for r in rows:
        if r.bulk_read_failures:
            notes.append(
                f"WARNING: {r.label}: {r.bulk_read_failures} bulk_read call(s) failed; "
                "this run does not measure a working Shunt."
            )
    return "\n".join(out) + "\n\n" + "\n".join(notes)


def main(args: list[str]) -> None:
    paths: list[str] = []
    for a in args or ["logs"]:
        if Path(a).is_dir():
            paths += [info.name for info in list_eval_logs(a)]
        else:
            paths.append(a)
    print(fmt_table([summarize(p) for p in sorted(paths)]))


if __name__ == "__main__":
    main(sys.argv[1:])
