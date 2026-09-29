# ai-comparison-framework

Side-by-side comparison of AI models and agent configurations (tokens, cost, time,
correctness, full transcripts), built on [Inspect AI](https://inspect.aisi.org.uk)
and `inspect_swe` (Claude Code / Codex CLI / Gemini CLI as Inspect agents).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                      # offline checks of fixtures and scorers (no API calls)
```

API keys go in a `.env` file in the repo root (gitignored; Inspect loads it automatically) or
your shell environment: `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`. Docker must be
running (the `docker` CLI + compose on PATH).

**WSL + Rancher Desktop:** if image builds fail with `error getting credentials`
(`docker-credential-wincred.exe: executable file not found`, or
`docker-credential-secretservice: ... libsecret-1.so.0`), set `"credsStore": "none"` in
`~/.docker/config.json`. Deleting the entry isn't enough: Docker then auto-picks
`docker-credential-secretservice` from Rancher's `linux/bin` (on the interactive PATH), which
needs a keyring WSL doesn't have. Public image pulls don't need credentials.

### First live check (costs well under $0.05)

```bash
inspect eval aicmp/tasks/smoke.py -T harness=react       --model anthropic/claude-haiku-4-5
inspect eval aicmp/tasks/smoke.py -T harness=claude_code --model anthropic/claude-haiku-4-5
```

Both should score `accuracy 1.000`. The first checks the key and the sandbox; the second also
checks the real Claude Code binary running through Inspect's bridge.

## Tasks (`aicmp/tasks/`)

| Task | Compares | Scoring |
|------|----------|---------|
| `code_review.py` | model A vs model B finding 3 planted bugs (decoy-free functions included) | recall of planted bugs |
| `needle_search.py` | `-T variant=single` vs `-T variant=subagent` over 12 large lorem-ipsum files with near-miss distractors | answer contains expected name + value |
| `large_read.py` | real Claude Code, `-T variant=direct` vs `-T variant=shunt` (home-grown replica of Spotify's Shunt: read-blocking hook + `bulk_read` tool on a cheap delegate model) over a synthetic 11k-line codebase | final `ANSWER:` line has required terms and none of the forbidden ones |
| `smoke.py` | end-to-end wiring check (`-T harness=react\|claude_code`) | reply contains a codeword |
| `plan_execute_review.py` | `-T variant=single` vs `-T variant=three_phase` (planner → executor → reviewer, fresh sessions, context via PLAN.md/NOTES.md/REVIEW.md) on a TokenBucket task | 20 hidden pytest tests derived from the acceptance criteria |

```bash
# model comparison (model ids are examples; use whatever you have access to)
inspect eval aicmp/tasks/code_review.py --model anthropic/claude-sonnet-5,google/gemini-2.5-pro --epochs 5
# approach comparison
inspect eval aicmp/tasks/needle_search.py -T variant=single   --model anthropic/claude-sonnet-5 --epochs 5
inspect eval aicmp/tasks/needle_search.py -T variant=subagent --model anthropic/claude-sonnet-5 --epochs 5
inspect view                # browse transcripts, token usage, scores
```

Add `--model-cost-config aicmp/model_costs.yaml` to see $ in `inspect view`, and run
`python -m aicmp.report logs/` for a side-by-side table (per-sample tokens and $, main model
vs delegate).

## Shunt experiment (`large_read.py`)

Tests the claim in Spotify's ["Portal cut my Claude Code token usage by 90%"](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90)
without depending on Portal. The replica (`aicmp/shunt/`):

- **Hook:** `.claude/settings.json` + `shunt_check.py` are dropped into the sandbox workspace
  as a PreToolUse hook. It blocks `Read` of files over 350 lines (`SHUNT_MIN_LINES`) unless
  `limit` <= 350, and blocks unpiped `cat`/`head`/`tail` of large files. Grep and piped
  commands still pass through.
- **bulk-reader:** `bulk_read(paths, question)` is a host-side Inspect tool exposed to Claude
  Code over MCP (`mcp__shunt__bulk_read`). It sends whole files to the `delegate` model role
  (default `google/gemini-3.8-flash`, temperature 0.2, the post's system prompt), so the
  delegate's tokens are logged and priced next to Claude's. The post used 2.5 Flash, but
  Google returns 404 for it on new API accounts. 3.8 Flash costs 2.5x as much ($0.75/$3.75
  per MTok, doubling in 2027), which narrows the price gap to Sonnet 5 from ~7x to ~2.7x.
  Try `--model-role delegate=anthropic/claude-haiku-4-5` for a single-vendor setup.
- Each shunt sample first checks that the delegate responds, and fails if it doesn't.
  Otherwise Claude quietly works around a broken tool and the run looks valid. The report
  shows `bulk_read` calls and hook blocks per sample, so you can see how much Shunt was used.
- Not replicated: `code-writer`, and Portal's 30s cap.

```bash
inspect eval aicmp/tasks/large_read.py -T variant=direct --model anthropic/claude-sonnet-5 --epochs 3 --model-cost-config aicmp/model_costs.yaml
inspect eval aicmp/tasks/large_read.py -T variant=shunt  --model anthropic/claude-sonnet-5 --epochs 3 --model-cost-config aicmp/model_costs.yaml \
    --model-role delegate=google/gemini-3.8-flash
python -m aicmp.report logs/
```

The post measured only Claude's tokens. The report shows that number ("main tok"), and also
cache reads (billed at 0.1x, which is what most of a direct read turns into after the first
turn), delegate cost, total $, and accuracy. A 90% token drop doesn't mean a 90% cost drop.

## Known limitations (current state)

- Tasks run in a Docker sandbox (`aicmp/docker`: python + pytest + curl, no network, 2GB/1 CPU).
  `AICMP_SANDBOX=local` runs on the host instead (no isolation). The no-network setting must be
  relaxed for tasks or MCPs that need internet. `curl` is required by `inspect_swe` for
  bridged tools.
- `code_review`, `needle_search`, and `plan_execute_review` use Inspect's native `react` agent;
  only `large_read` and `smoke` use the real Claude Code CLI so far.
- Verified with mock models only: Claude Code installs and runs in the sandbox, the Shunt hook
  blocks a large `Read` and allows a targeted slice, and `bulk_read` reaches the delegate model
  with its usage logged separately. No live Anthropic run yet. Regex scoring in
  `code_review.py` may need tuning after real transcripts.

## `react` model comparison vs. real vendor CLIs (`inspect_swe`)

Two different things can be compared here, and it matters which one a given eval is doing:

1. **Model-only comparison** (what the three tasks above do today): every variant uses Inspect's
   own `react` agent — its own system prompt, its own bash tool, no `CLAUDE.md`/skills/subagents.
   Only the underlying model differs. Clean, but tells you nothing about Claude Code, Codex CLI,
   or Gemini CLI as products.
2. **Real harness comparison**: `inspect_swe` provides `claude_code()`, `codex_cli()`, and
   `gemini_cli()` as drop-in Inspect agents that run the *actual* CLI binaries (their real system
   prompts, tools, subagent/skills/`CLAUDE.md` handling, compaction, etc.) inside the sandbox. Not
   yet wired into any task here — planned next.

### How `claude_code()` actually authenticates (read from `inspect_swe` source, not just docs)

- It installs the real Claude Code binary in the sandbox container and points it at
  `ANTHROPIC_BASE_URL=http://localhost:<port>`, a proxy running inside the container
  (`sandbox_agent_bridge()`), with a dummy `ANTHROPIC_AUTH_TOKEN`.
- The proxy relays every request to **Inspect's own model provider on the host**, which is what
  holds real credentials and does the actual API call. Tokens/cost/transcript are recorded there.
- It seeds `~/.claude/settings.json` with an `apiKeyHelper` and marks onboarding complete so the
  CLI never tries an interactive OAuth flow inside the container.
- **Consequence: an existing Claude Code subscription login (Pro/Max) does not carry over.** The
  host-side Inspect Anthropic provider needs `ANTHROPIC_API_KEY` (a console.anthropic.com API key,
  billed separately from a Claude subscription). The container never sees real credentials
  regardless.
- Untested/unverified escape hatch: Inspect's Anthropic provider also reads `ANTHROPIC_AUTH_TOKEN`
  (OAuth bearer), so a token from `claude setup-token` might work in place of an API key — but
  whether Anthropic's terms permit that for this kind of automated/parallel use is unverified, so
  don't build on it without checking. Treat API key as the supported path.
- It also sets `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, disables experimental betas and
  auto-memory, and blocks web_search/web_fetch/remote MCP unless explicitly granted — real Claude
  Code behavior, but not bit-for-bit identical to an interactive session.
