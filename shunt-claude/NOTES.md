# Shunt for Claude Code, with Haiku 5.5 as the cheap reader

Presentation notes. Measured numbers are marked **measured**. Anything I calculated is marked
**estimate**. Run on 2026-10-14. I first built this with Devin + SWE-1.6 as the reader, then
swapped to Claude Haiku 5.5 so the whole stack is one vendor. Devin results are kept below as
a comparison point.

## The pitch in one paragraph

Claude Code spends most of its tokens *reading*. Spotify's Shunt pattern stops the expensive
model from reading big files at all. A hook blocks large reads and redirects Claude to a
`bulk_read` tool. That tool hands the files to a cheaper model, which returns a short answer.
The files never enter the main model's context. This repo already had a replica that ran in an
Inspect sandbox with Gemini as the reader. This work ports it to a real Claude Code setup with
**Claude Haiku 5.5** as the reader, called through headless `claude -p`.

## How it works

```
Claude Code (Sonnet/Opus)
  │  Read big_file.py ──▶ PreToolUse hook (shunt_check.py) ──▶ blocked, "use bulk_read"
  │
  └─ mcp__shunt__bulk_read(paths, question)
        │   shunt-claude/bulk_read_mcp.py  (stdio MCP server, stdlib only)
        ▼
     claude -p --model claude-haiku-5-5 --tools "" --system-prompt <bulk-reader prompt>
            --strict-mcp-config --setting-sources "" --output-format json   (files on stdin)
        │   DISABLE_PROMPT_CACHING=1, empty temp dir, no tools, no MCP, no settings
        ▼
     short answer ──▶ Claude;  tokens + real $ ──▶ ~/.claude/shunt/usage.jsonl
```

| Piece | File | Change from the Gemini replica |
|---|---|---|
| Hook | `aicmp/shunt/claude/hooks/shunt_check.py` | None. Reused as is. |
| Reader tool | `shunt-claude/bulk_read_mcp.py` | New. A host-side Inspect tool became a standalone MCP server. The Gemini API call became `claude -p` on Haiku 5.5. |
| Install | `shunt-claude/install.py` | New. Merges the hook and registers the MCP server (`--scope project` or `user`). |
| Tests | `tests/test_shunt_claude.py` | New. Uses a fake `claude` binary, no spend. |

Design choices worth defending:
- **Child process is locked down.** `--tools ""` gives the reader no tools, `--strict-mcp-config`
  with no config loads no MCP servers, and `--setting-sources ""` loads no settings or hooks.
  That last one matters: without it, a user-level install would make the reader load the shunt
  itself and recurse.
- **Real cost, not estimated.** `--output-format json` returns `total_cost_usd` and token
  counts, so the usage log holds what Claude Code computed rather than my arithmetic.
- **Same bulk-reader prompt** as the Spotify post ("precise code analyst… structured bullets
  only"), so results are comparable with the Gemini variant. It goes in as the system prompt.
- **Fail loudly.** Oversize input or a reader failure returns a tool error, never an empty
  answer that Claude would read as "nothing found".
- **Uses the CLI's existing login.** No separate API key or provider setup.

## The pricing cliff (most useful finding)

Haiku 5.5 is $0.10 in / $0.50 out per MTok, **only for prompts up to 100K tokens**. Above
that, the *whole* prompt is billed at $0.50 / $2.50 (source: Anthropic model table, claude-api
skill, cached 2026-10-06). I hit this by accident on the first live run:

| Run (same question, 377 KB / ~149K tokens) | Cost | Why |
|---|---|---|
| Default Claude Code caching | **$0.151** (measured) | 149K-token cache write at about 2x input price, which Claude Code uses for its 1h cache, and never read back |
| Caching off | **$0.078** (measured) | 149K tokens × $0.50 + 1.3K out × $2.50. Matches the logged cost to the cent. |
| Under 100K tokens, caching off | **$0.0059** (measured, 137 KB / 54K tokens) | $0.10 tier |

So two defaults are baked in: `DISABLE_PROMPT_CACHING=1` for the reader (every call is one-shot)
and a 240 KB input cap (about 95K tokens) that refuses oversize reads with a "narrow the paths"
error. **Estimate:** the full 377 KB corpus as two calls under the cap would cost about
$0.015 plus output, roughly 5x less than one oversize call.

## Real-repo results: `tracking` (Go service, 1.6K files)

Same prompt, same main model (Sonnet 5.5), reader Haiku 5.5 (effort low), read-only. Run by
`shunt-claude/demo/run_ab.py`, which injects the shunt with `--settings`/`--mcp-config` so
nothing is written into the repo. Three arms: **direct** (stock), **shunt** (hook + tool),
**guided** (shunt + a system-prompt line telling Claude when to offload, which is the "skills"
layer of the original Shunt). n=2-3 per cell, so treat as directional.

**Question 1: "document the MacroPoint gateway package"** (reads about 30 files across 4 dirs)

| Arm | Runs | `bulk_read` used | Total $ | Peak context | Wall time |
|---|---|---|---|---|---|
| direct | 3 | 0 | $0.343 – $0.363 (mean $0.355) | 78K – 88K | 56 – 60s |
| shunt / guided, **when it used `bulk_read`** | 5 | 1 – 4 calls | $0.192 – $0.255 (mean $0.225) | 41K – 51K | 137 – 198s |
| shunt, when it **skipped** `bulk_read` | 1 | 0 | $0.390 | 88K | 68s |

When the shunt engaged: about **37% cheaper** and **about 45% less peak context**, but about
**3x slower**. The reader costs only $0.016 – $0.029 per run; the slowness is the reader
writing 5-30K output tokens per call. When it didn't engage, the run was the most expensive
of the set.

**Question 2: "trace a MacroPoint tracking-map save end to end"** (entry point known; the
flow touches about 6 files, most under 350 lines)

| Arm | Runs | `bulk_read` calls | Mean total $ | Peak context |
|---|---|---|---|---|
| direct | 3 | 0 | $0.353 | 70K – 84K |
| shunt | 3 | **0** | $0.299 | 63K – 70K |
| guided | 2 | **0** | $0.414 | 78K – 86K |
| strict hook (below) | 3 | 4 calls in 1 run, 0 in 2 | $0.343 | 68K – 76K |

**The shunt never fired**, even with the guidance line. Claude used `Grep` plus targeted
`Read` slices and peaked at only 70-85K of context. The differences between arms are run-to-run
noise, not a shunt effect. Cost per run on this question is about $0.30-0.45 either way.

**"Doesn't Spotify's hook fire here?"** It does, and it is the stock hook in the *shunt* arm
(block any `Read` of a file over 350 lines unless `limit` <= 350). In the trace it fired on the
only two files over 350 lines (`client.go` 478, `requested.go` 451): Claude was blocked once,
then read them in slices. The other 6-8 files it read per run were all under 350 lines, so a
per-file threshold never sees them even though together they are most of the context.

So I tried a stricter hook (`SHUNT_TRACK_PAGING=1` counts slices of one file together;
`SHUNT_SESSION_LINES=1200` blocks further reads of files over 100 lines once 1200 lines have
been read directly). It blocked 3-5 times per run, and still **did not change the outcome**:
Claude answered each block by retrying with smaller slices and more `Grep`, and called
`bulk_read` in only 1 of 3 runs. Cost and peak context were flat ($0.343 vs $0.353 direct).
The one run that did delegate cost the most ($0.392) and its answer missed two function names
(`persistCreatedOrder`, `handleVendorFailure`) that the direct runs found, because the reader's
summary dropped detail the main model would have read. A hook can only deny a call; it can't
make the model choose the delegate, and it can't delegate for it, because a `Read` call
carries no question for the reader to answer.

**Takeaway:** for tracing work where Claude already follows the code with grep and short
slices, tuning the hook does not help. The shunt's value is on broad, read-everything tasks.

**Answer quality (blinded fact-check, Question 1).** A separate agent checked 12 claims per
doc against the code without knowing the arm:

| Arm | Wrong claims | Completeness (1-5) |
|---|---|---|
| direct #1 / #2 | 1 / 0 | 4 / 5 |
| shunt #2 (used `bulk_read`) | 1 | 5 |
| guided #1 / #2 | 1 / 2 | 5 / 4 |

All five covered every file. Shunt/guided docs had **3 wrong claims in 3 docs vs. 1 in 2 for
direct**. The shunt errors were cross-package wiring claims ("wired in `cmd/tracking/main.go`"
when it is `cmd/macropoint/main.go`; "`service` imports `guard`" when only the consumer does).
The reader sees only the files it was given, so it can't know who calls what. That is a
plausible failure mode, but n=5 can't prove it. Shunt/guided docs also had the deepest
edge-case coverage (e.g. a non-rolled-back commit failure). The `file:line` references in the
one guided doc that had any were 3/3 in range (range check only, not correctness).

**What this means for the pitch:**
- The shunt pays off on **broad reads** (document a package, audit a directory) and does
  nothing on **targeted traces** where Claude already navigates with grep and slices.
- Savings are about a third of the dollars, not 90%. The dominant effect is **smaller main
  context** (matters more on long sessions, where context compounds), not the dollar delta
  on a single question.
- You pay in latency, 3x here. Don't hide it.
- Engagement is not guaranteed. In the hook-only arm the model used the tool in 2 of 3 broad
  runs; the guidance line made it 3 of 3. For targeted traces it was 0 of 5 either way.

## What I verified (measured)

1. **Reader quality.** Both Haiku calls got the benchmark question right
   (`ledger.payments.capture.retry.v2`, 9). It cited the files, rejected the `retry.v1`
   distractor, and noted that `6` is only a fallback default. This is slightly more detailed
   than SWE-1.6's answer.
2. **Cost and speed under the cap:** 137 KB, 54K tokens, 918 output tokens, **$0.0059, 5.9s**.
3. **Wiring in real Claude Code, end to end with Haiku as the reader.** On the real `tracking`
   repo the MCP server showed `connected`, Claude called `mcp__shunt__bulk_read`, and the hook
   blocked large `Read`s. See the next section for what that did to cost.
4. Offline tests pass (24 total, 4 new for this server). No spend.

## Findings you should present honestly

1. **Claude mostly doesn't need the shunt on lookup questions.** On two benchmark questions
   (refund error code, default-enabled flags) Claude Haiku and Sonnet used `grep`/`sed` and
   answered correctly. The hook lets grep through, so the shunt never fired. Sonnet's whole run
   cost $0.074. The shunt helps when the task needs *whole-file understanding*: summaries,
   flow tracing, audits.
2. **The hook has a paging loophole.** Asked to read a large file in full, Haiku was blocked
   once, then read it in `limit: 350` slices, which the hook allows by design. A fix would be
   a stateful hook that totals lines read per file per session. Not built.
3. **Small samples.** The real-repo A/B above is n=2-3 per cell. The Inspect `large_read` task
   (synthetic corpus, 3+ epochs, scored answers) is still the place for a statistically
   honest number.
4. **A 90% token cut is not a 90% cost cut.** Most of a direct read becomes cache reads at
   0.1x after the first turn (see the main README). Quote dollars, not tokens.
5. **Project-scoped MCP needs one-time approval** in the CLI. The `permissions.allow` entry
   in project settings was not honored in my headless `-p` runs, and I didn't find out why.
   Verify before a live demo.
6. **Same-vendor cost caveat.** The reader runs on whichever auth the `claude` CLI has. On a
   subscription login the dollar figure is notional; on an API key it is the real bill.

## Reader comparison (same corpus, same question)

| Reader | Cost | Time | Context | Notes |
|---|---|---|---|---|
| **Haiku 5.5** via `claude -p`, ≤100K tokens | $0.0059 for 54K tokens (**measured**) | 5.9s | 1M | About 3.4K tokens fixed overhead (**measured**) |
| Haiku 5.5, >100K tokens, no cache | $0.078 for 149K tokens (**measured**) | 7.2s | 1M | 5x tier jump |
| SWE-1.6 via Devin | $0.054 for 110K tokens (**measured**, list price) | 12.2s | 200K | $0.50/$2.50, about 12K tokens fixed overhead, needs Devin account, data goes to Cognition |
| Gemini 3.8 Flash (original) | not run | | 1M | $0.75/$3.75 |
| Sonnet 5 as the reader | not run | | 1M | $2/$10; **estimate** about $0.11 for 54K tokens |

Tokenizers differ (SWE-1.6 counted 110K tokens for the bytes Haiku counted as 149K), so
compare dollars, not token counts. **Estimate** for the same 54K-token read on SWE-1.6: about
$0.027 plus overhead, so Haiku in-tier is roughly 4x cheaper and about 2x faster.

## Open questions and risks for the room

- **Reader accuracy at scale.** One needle question is not a benchmark. The remaining
  questions in `aicmp/data/codebase_gen.py` should be run through the reader.
- **Reader quality vs. cost.** Haiku 5.5 at default effort was good here. A lower effort level
  or a different model (`SHUNT_DELEGATE_MODEL`) is one env var away. Worth sweeping.
- **Input cap vs. big tasks.** 240 KB per call means large audits take several calls. An
  auto-splitting `bulk_read` is a possible next step.
- **Not replicated:** Shunt's `code-writer` mode and Portal's 30s cap, as in the original
  replica.

## Demo script

```bash
# 0. one-time
pip install -e ".[dev]" && pytest tests/test_shunt_claude.py

# 1. the reader on its own, no main-model session needed
python3 - <<'EOF'
import sys; sys.path.insert(0, "shunt-claude")
from bulk_read_mcp import bulk_read
print(bulk_read(["<codebase dir under 240 KB>"], "Which queue does a failed capture retry to?"))
EOF
cat ~/.claude/shunt/usage.jsonl                 # tokens, real $, seconds per call

# 2. install into a project, restart Claude Code, approve the server
python3 shunt-claude/install.py                 # or --scope user
#    /mcp should list "shunt". Ask Claude to summarize a 700+ line file.

# 3. swap the reader
SHUNT_DELEGATE_MODEL=claude-sonnet-5-5 claude   # or any model your login can use
# remove with: python3 shunt-claude/install.py --uninstall
```

## Showing token usage live during the demo

Three built-in ways, from least to most effort:
1. **`/context`** prints a coloured grid of what fills the context window (system, tools,
   messages). Run it before and after a broad read to show the difference.
2. **`/usage`** (alias `/cost`) shows session cost, API vs wall time and per-model
   input/output/cache tokens. Subscription logins show plan bars; the dollar figure is an
   estimate at list price.
3. **A custom status line** that updates as you work. `shunt-claude/demo/statusline.py`
   shows two lines:
   ```
   Sonnet 5.5 ██░░░░░░░░░░░░░░░░░░ 12% 124.0K/1.00M ctx · out 3.2K · $0.413 main
   shunt 1 bulk_read · 53.9K tokens kept out of main context (reader returned 918) · $0.0059 reader
   ```
   Line 1 comes from Claude Code's status-line JSON (`context_window.*`, `cost.total_cost_usd`);
   line 2 sums the shunt's usage log. It refreshes after each assistant message and every
   2s via `refreshInterval`.

Side-by-side launcher, nothing written into the repo (temp dir, `--settings`, `--mcp-config`):

```bash
shunt-claude/demo/live.sh ../tracking direct   # left terminal: stock Claude Code
shunt-claude/demo/live.sh ../tracking shunt    # right terminal: hook + Haiku reader + guidance
# same prompt in both, e.g. "Write a developer doc for internal/macropoint-gateway. Source
# code only, no .wiki/docs/tests, no file changes."
```

Offline, repeatable version of the same comparison (prints the tables above):

```bash
python3 shunt-claude/demo/run_ab.py --repo ../tracking --question gateway_docs --runs 2
```

**Not verified:** the status line has only been checked against sample JSON, because it only
runs in an interactive session. Check it renders before presenting. Also pick a **broad** prompt
for the live run; a targeted trace will show the shunt doing nothing.

Hook modes (all opt-in, set in the environment Claude Code runs in): `SHUNT_TRACK_PAGING=1`,
`SHUNT_SESSION_LINES=N`, `SHUNT_SESSION_MIN_LINES` (default 100). `live.sh ... strict` and
`run_ab.py --arms strict` use them.

Config env vars: `SHUNT_DELEGATE_MODEL`, `SHUNT_EFFORT`, `SHUNT_CLAUDE_BIN`, `SHUNT_MAX_BYTES`,
`SHUNT_TIMEOUT`, `SHUNT_LOG`, and the hook's `SHUNT_MIN_LINES` (default 350).

## Suggested next steps

1. More epochs on the `tracking` A/B (`--runs 5`) and a question that spans a whole service
   (audit, migration impact), where the shunt should help most.
2. Stateful hook to close the paging loophole.
3. A `large_read` variant with this reader, so direct vs Gemini vs Haiku 5.5 can be compared
   in dollars on the same questions.
4. Per-session report from `usage.jsonl` (calls, delegate $, bytes kept out of context).
