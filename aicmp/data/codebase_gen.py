"""Deterministic generator for a synthetic Python codebase ("ledgerline").

Twelve modules of 600-1500 lines of plausible filler code, with a handful of real facts
planted among them. Answering the questions requires following references across files
(a literal default overridden in config, an exception mapped to a status code elsewhere,
flags scattered over many modules), so neither a single grep nor a single file read is
enough. The model has never seen this code, so it can't answer from memory.

Output goes to aicmp/data/codebase/ (gitignored).
"""

import random
import textwrap
from pathlib import Path

OUT = Path(__file__).parent / "codebase"
SEED = 20260926
MIN_LINES, MAX_LINES = 600, 1500

MODULES = [
    "ledgerline/core/utils.py",
    "ledgerline/config/defaults.py",
    "ledgerline/messaging/queues.py",
    "ledgerline/payments/capture.py",
    "ledgerline/payments/refunds.py",
    "ledgerline/api/errors.py",
    "ledgerline/ledger/entries.py",
    "ledgerline/ledger/reconcile.py",
    "ledgerline/accounts/profiles.py",
    "ledgerline/risk/scoring.py",
    "ledgerline/reporting/exports.py",
    "ledgerline/notifications/dispatch.py",
]

# --- planted facts -------------------------------------------------------------------
# Each snippet is inserted verbatim at a random position among the filler of its module.

PLANTED: dict[str, list[str]] = {
    "ledgerline/core/utils.py": [
        '''
        _FLAGS: dict[str, bool] = {}


        def register_flag(name: str, default: bool) -> None:
            """Register a feature flag with its default state."""
            _FLAGS.setdefault(name, default)


        def env_bool(var: str, fallback: bool) -> bool:
            raw = os.environ.get(var)
            return fallback if raw is None else raw.lower() in ("1", "true", "yes")


        def settings_get(key: str, fallback: object) -> object:
            """Look up an operational setting; `fallback` applies only if unset in config."""
            from ledgerline.config.defaults import SETTINGS

            return SETTINGS.get(key, fallback)


        def _coerce(value: object, scale: int) -> object:
            return value * scale if isinstance(value, int) else value
        ''',
    ],
    "ledgerline/config/defaults.py": [
        '''
        # Operational overrides. Values here win over literal fallbacks in modules.
        SETTINGS: dict[str, object] = {
            "capture.max_attempts": 9,
            "capture.timeout_s": 40,
            "refund.window_days": 120,
            "export.batch_size": 5000,
        }
        ''',
    ],
    "ledgerline/messaging/queues.py": [
        '''
        QUEUE_NAMES: dict[str, str] = {
            "capture_retry": "ledger.payments.capture.retry.v2",
            "refund_events": "ledger.payments.refunds.v1",
            "receipt_dispatch": "ledger.notify.receipts.v3",
        }

        # Kept for consumers that have not migrated yet. Do not publish here.
        LEGACY_QUEUE_NAMES: dict[str, str] = {
            "capture_retry": "ledger.payments.capture.retry.v1",
        }
        ''',
    ],
    "ledgerline/payments/capture.py": [
        '''
        MAX_CAPTURE_ATTEMPTS = settings_get("capture.max_attempts", 6)


        def handle_capture_failure(charge: dict, attempt: int) -> None:
            """Schedule another capture attempt unless we have exhausted retries."""
            if attempt >= MAX_CAPTURE_ATTEMPTS:
                mark_charge_failed(charge["id"], reason="capture_exhausted")
                return
            publish(QUEUE_NAMES["capture_retry"], {"charge_id": charge["id"], "attempt": attempt + 1})
        ''',
        '''
        register_flag("instant_payouts", default=True)
        ''',
    ],
    "ledgerline/payments/refunds.py": [
        '''
        def validate_refund(charge: dict, amount_cents: int, age_days: int) -> None:
            """Reject refunds that the processor would decline anyway."""
            if age_days > settings_get("refund.window_days", 90):
                raise RefundWindowExpired(charge["id"])
            if amount_cents > charge["captured_cents"]:
                raise RefundExceedsCharge(charge["id"], amount_cents)
        ''',
    ],
    "ledgerline/api/errors.py": [
        '''
        # Domain exception -> (HTTP status, machine-readable error code)
        ERROR_MAP: dict[type, tuple[int, str]] = {
            ChargeNotFound: (404, "CHARGE_NOT_FOUND"),
            RefundWindowExpired: (409, "REFUND_WINDOW_CLOSED"),
            RefundExceedsCharge: (422, "REFUND_OVER_CAPTURE"),
            DuplicateIdempotencyKey: (409, "IDEMPOTENCY_REPLAY"),
        }
        ''',
    ],
    "ledgerline/accounts/profiles.py": ['register_flag("legacy_csv_export", default=False)\n'],
    "ledgerline/risk/scoring.py": ['register_flag("risk_v3_scoring", default=True)\n'],
    "ledgerline/reporting/exports.py": ['register_flag("manual_review_queue", default=False)\n'],
    "ledgerline/notifications/dispatch.py": [
        'register_flag("sms_fallback", default=False)\n',
        'register_flag("async_receipts", default=env_bool("ASYNC_RECEIPTS", True))\n',
    ],
}

# Every question asks for a final "ANSWER:" line; the scorer checks only that line.
# Target: '|'-separated strings that must appear; a leading '!' means must NOT appear.
QUESTIONS = [
    {
        "id": "capture_retry",
        "input": (
            "When a card payment capture fails, which message queue is the retry published "
            "to, and what is the effective maximum number of capture attempts at runtime?"
        ),
        "target": "ledger.payments.capture.retry.v2|9|!retry.v1",
        "answer_format": "ANSWER: <queue name>, <max attempts>",
    },
    {
        "id": "refund_over_capture",
        "input": (
            "If a client requests a refund larger than the amount originally captured, what "
            "HTTP status code does the API respond with, and what error code string?"
        ),
        "target": "422|REFUND_OVER_CAPTURE|!REFUND_WINDOW_CLOSED",
        "answer_format": "ANSWER: <status>, <error code>",
    },
    {
        "id": "enabled_flags",
        "input": (
            "Which feature flags registered with register_flag are enabled by default "
            "(assume no environment variables are set)? Give their exact names."
        ),
        "target": (
            "instant_payouts|risk_v3_scoring|async_receipts"
            "|!legacy_csv_export|!sms_fallback|!manual_review_queue"
        ),
        "answer_format": "ANSWER: <comma-separated flag names>",
    },
]

PROMPT_TEMPLATE = (
    "The Python codebase in the current directory (./ledgerline) is a payments service. "
    "{question}\n\nThe code is large; investigate it with the tools you have. Do not modify "
    "any files. Finish with a single final line in exactly this form:\n{answer_format}"
)

# --- filler ---------------------------------------------------------------------------

VERBS = "normalize resolve compute derive assemble project merge validate hydrate collect partition rank summarize annotate reconcile".split()
NOUNS = "ledger invoice account statement batch cursor window bucket snapshot tenant merchant payout balance journal posting schedule cohort tier segment".split()
FIELDS = "currency region tenant_id posted_at amount_minor external_ref status channel settlement_day fx_rate cohort sequence".split()


def _ident(rng: random.Random) -> str:
    return f"{rng.choice(VERBS)}_{rng.choice(NOUNS)}_{rng.choice(NOUNS)}"


def _filler_function(rng: random.Random) -> str:
    name, f1, f2 = _ident(rng), rng.choice(FIELDS), rng.choice(FIELDS)
    n, noun = rng.randint(2, 97), rng.choice(NOUNS)
    kind = rng.randrange(4)
    if kind == 0:
        body = f'''
        def {name}(record: dict, *, strict: bool = False) -> dict:
            """{name.split("_")[0].capitalize()} the {noun} fields used by downstream consumers."""
            result = dict(record)
            if strict and "{f1}" not in result:
                raise ValueError("missing {f1}")
            result["{f1}"] = _coerce(result.get("{f1}"), {n})
            if result.get("{f2}") is None:
                result["{f2}"] = _DEFAULTS.get("{f2}")
            return result
        '''
    elif kind == 1:
        body = f'''
        def {name}(rows: list[dict], limit: int = {n * 10}) -> list[dict]:
            """Return at most `limit` {noun} rows ordered by {f1}."""
            ordered = sorted(rows, key=lambda r: (r.get("{f1}") or 0, r.get("{f2}") or ""))
            out = []
            for row in ordered:
                if len(out) >= limit:
                    break
                if row.get("status") in _SKIP_STATUSES:
                    continue
                out.append(row)
            return out
        '''
    elif kind == 2:
        cls = "".join(w.capitalize() for w in name.split("_")[1:]) + rng.choice(["Builder", "View", "Index", "Cache"])
        body = f'''
        class {cls}:
            """In-memory {noun} helper keyed by {f1}."""

            def __init__(self, capacity: int = {n * 8}) -> None:
                self._capacity = capacity
                self._items: dict[str, dict] = {{}}

            def put(self, item: dict) -> None:
                key = str(item.get("{f1}"))
                if len(self._items) >= self._capacity:
                    self._items.pop(next(iter(self._items)))
                self._items[key] = item

            def get(self, key: str) -> dict | None:
                return self._items.get(key)

            def total(self) -> int:
                return sum(int(i.get("{f2}") or 0) for i in self._items.values())
        '''
    else:
        body = f'''
        def {name}(values: list[int], threshold: int = {n}) -> dict[str, int]:
            """Bucket {noun} values around `threshold`."""
            buckets = {{"low": 0, "mid": 0, "high": 0}}
            for v in values:
                if v < threshold:
                    buckets["low"] += 1
                elif v < threshold * {rng.randint(2, 9)}:
                    buckets["mid"] += 1
                else:
                    buckets["high"] += 1
            return buckets
        '''
    return textwrap.dedent(body).strip("\n") + "\n"


HEADER = '''"""{module} - part of the ledgerline payments service."""

import os
{imports}
_DEFAULTS: dict[str, object] = {{}}
_SKIP_STATUSES = frozenset({{"void", "archived"}})
'''


def _module(rel: str, rng: random.Random) -> str:
    target = rng.randint(MIN_LINES, MAX_LINES)
    blocks: list[str] = []
    while sum(b.count("\n") + 2 for b in blocks) < target:
        blocks.append(_filler_function(rng))
    for snippet in PLANTED.get(rel, []):
        pos = rng.randint(len(blocks) // 4, 3 * len(blocks) // 4)
        blocks.insert(pos, textwrap.dedent(snippet).strip("\n") + "\n")
    imports = "" if rel.endswith("core/utils.py") else (
        "\nfrom ledgerline.core.utils import _coerce, env_bool, register_flag, settings_get  # noqa: F401\n"
    )
    return HEADER.format(module=rel, imports=imports) + "\n\n" + "\n\n".join(blocks)


def generate(force: bool = False) -> Path:
    if OUT.exists() and not force and all((OUT / m).exists() for m in MODULES):
        return OUT
    rng = random.Random(SEED)
    for rel in MODULES:
        path = OUT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_module(rel, rng))
        (path.parent / "__init__.py").touch()
    (OUT / "ledgerline" / "__init__.py").touch()
    return OUT


if __name__ == "__main__":
    print(generate(force=True))
