# token-diet

Route the mechanical parts of a Claude Code session to cheap models, and keep
unfiltered command output out of the conversation.

Inspired by [Portal by Spotify cut my Claude Code token usage by 90%](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90).
Do not expect 90%. That number assumes a file is re-sent at full price every
turn; with prompt caching, later requests re-read it at the cache-read rate
(0.1x input), so the naive saving is an order of magnitude smaller than it
looks.

**Read [What is actually measured](#what-is-actually-measured) before adopting
this.** Two of the three pieces have a measured result and one of them is
negative for Haiku.

## What's in it

- **`bulk-reader`** (subagent) - reads named large files, returns bullets with
  `file:line` anchors. The file stays in the subagent's context; only the
  bullets reach the caller.
- **`code-writer`** (subagent) - writes mirror-pattern code (a test like the
  twenty next to it, config, scaffolding) from a spec plus a **required**
  reference file, then runs the narrowest check and iterates until it passes.
  Refuses to work without a reference.
- **A PreToolUse hook** (`filter_bash_output.py`) - stops shell commands from
  dumping an unfiltered payload into the session.

## The hook rewrites where it can, and only asks where it can't

A hook that refuses commands makes the model retry, and a retry costs more
than the payload sometimes does. So the hook applies one rule: **can the fix be
made without deciding anything on the model's behalf?**

- **Rewrite** (`permissionDecision: "allow"` + `updatedInput`) when the
  narrowing is mechanical and loses nothing the caller wanted. `pytest tests/`
  becomes `pytest tests/ -q --tb=short` in place. The model never sees a
  refusal, so there is no retry and no correction loop. This also removes the
  false-positive class entirely: `pytest --version` needs no allowlist because
  it is never refused.
- **Deny, with the fix in the message** when narrowing needs a decision the
  hook cannot make - which JMESPath for `az --query`, which `jq` path for a
  REST response. Silently truncating those would trade a visible retry for an
  invisible omission, which is the worse failure.

Append `#full` to any command to bypass both. Any internal error in the hook
allows the command through unchanged.

## What is actually measured

One repo, one session, one task each. Small sample - treat as a direction, not
a benchmark.

**Reading.** Target: a 1105-line C# service. Question: how is country/channel
scoping enforced, naming every database-querying method and calling out any
that applies no filter. Answers graded against the file.

| Variant | Tokens | Result |
|---|---|---|
| Haiku | 38k | Found the real anomaly, then **mischaracterized it as a defect and invented a supporting citation** to a convention file that does not contain the phrase. 2 of ~20 line anchors wrong (one pointed at a comment 6 lines above the method). Broke 3 of its own output rules. |
| Sonnet | 66k | Accurate anchors. Correctly identified the anomaly as intentional and cited the entity definition that makes it so. |
| Built-in `Explore` | 57k | Most thorough. Verified across files, and found two genuinely dead local variables that neither other variant reported. 2 anchors off by 1-2 lines. |

**Writing.** Target: add one xUnit test for an uncovered validator branch,
reference file named, style to be matched, test must pass.

| Variant | Tokens | Result |
|---|---|---|
| Haiku | 28k | Test passes. Matched the reference style. |
| Sonnet | 38k | Test passes. Matched the reference style. |

The two test implementations were near-identical; only the literal values
differed.

**So:** on mirror-pattern writing with a reference file and a passing check as
the gate, Haiku was indistinguishable from Sonnet and cheaper. On reading,
where the job is judging what matters, Haiku fabricated support for a wrong
conclusion - the failure mode a summary cannot show you, because you no longer
have the file. Prefer Sonnet for the reader. Consider whether you need a reader
at all, per below.

## How this relates to the built-in `Explore` agent

Claude Code ships [`Explore`](https://code.claude.com/docs/en/subagents), which
inherits the main conversation's model (capped at Opus) and already isolates
its reading from your context. It scored best in the read test above.

`bulk-reader` differs only in its **response contract**: bullets only, a
`file:line` anchor on every claim, an explicit bullet for what it could not
answer. If you like that contract, you can put the same instructions in a
custom agent named `Explore` and skip this file. The existence of a second
reader is not itself an argument.

## What this does and does not buy you

Anthropic's own [cost guidance](https://code.claude.com/docs/en/costs)
recommends both halves of this independently: `model: haiku` for simple
subagent tasks, delegating verbose operations to subagents, and hooks that
preprocess data before Claude sees it. So the patterns are not exotic. What is
worth being precise about is the payoff:

- **Context isolation is real.** A subagent's reads never enter the parent
  context. This is documented behaviour, not a trick, and you get it from
  `Explore` too.
- **Usage still scales with context on every request.** Even cached, the whole
  conversation is re-read each turn, so a 27k-token file kept out of context
  saves roughly 2.7k-equivalent per subsequent turn - real over a long
  session, and 10x less than the naive framing implies.
- **Delegating forfeits the parent's cache for that content.** A subagent
  starts on a fresh, uncached prefix.
- **Whether a cheaper model saves anything depends on how your plan meters.**
  Check `seatTier` before assuming. On a
  [seat-based Team/Enterprise plan](https://support.claude.com/en/articles/11845131-use-claude-code-with-your-team-or-enterprise-plan),
  the rolling 5-hour and weekly windows are shared across models, so a cheaper
  worker does not stretch them; only the per-model-family ceilings ("you've hit
  your Opus limit") respond to model choice. On a **usage-based** Enterprise
  plan there are no per-seat limits and usage is billed at API rates, so model
  choice moves cost directly and proportionally.
- **The right metric is cost per completed task**, not tokens. A cheap worker
  that misses something and forces the expensive model to redo the work is not
  cheaper.

If you already orchestrate well with `Explore`, subagents, or workflows, the
marginal gain here is small and the honest first step is to compare against
well-configured stock Claude Code.

## Install as a plugin

```
/plugin marketplace add matuspavliscak/token-diet
/plugin install diet@token-diet
```

## Setup tutorial for AI agents

If you are an AI coding agent asked to set this up in a repository, follow
these steps. Do not improvise beyond them.

1. Copy `plugins/diet/agents/bulk-reader.md` and
   `plugins/diet/agents/code-writer.md` into the target repo's
   `.claude/agents/` directory (create it if missing).
2. Copy `plugins/diet/hooks/filter_bash_output.py` into `.claude/hooks/`.
3. Merge this key into the repo's `.claude/settings.json` (or
   `settings.local.json` if the team does not commit hooks). If a `hooks` key
   already exists, append to the `PreToolUse` array instead of replacing it:

   ```json
   {
     "hooks": {
       "PreToolUse": [
         {
           "matcher": "Bash",
           "hooks": [
             {
               "type": "command",
               "command": "python3 \"$CLAUDE_PROJECT_DIR/.claude/hooks/filter_bash_output.py\"",
               "timeout": 10
             }
           ]
         }
       ]
     }
   }
   ```

4. Adapt the two agent files: each contains a section marked `ADAPT` with
   example repo conventions. Read the target repo (its data access layer,
   logging style, test layout) and replace the examples with 2-4 real rules
   from that repo. Delete examples that have no equivalent.
5. Adapt the hook: keep only the branches for CLIs the repo actually uses, and
   add branches for its own payload-heavy commands. Put a mechanical fix in the
   rewrite path and a judgement-dependent one in the deny path. Test it without
   burning a session:

   ```
   echo '{"tool_name":"Bash","tool_input":{"command":"pytest tests/"}}' \
     | python3 .claude/hooks/filter_bash_output.py
   ```

   A rewrite prints `"permissionDecision": "allow"` with an `updatedInput`
   command; a deny prints `"deny"` with the reason; a command the hook ignores
   prints nothing. Also check that the same command with ` #full` appended
   prints nothing.
6. Set the worker model deliberately. `model: sonnet` for the reader (see the
   measurements above); `model: haiku` is defensible for `code-writer`, where a
   passing check gates the output.
7. Tell the user to restart their Claude Code session - hooks load at startup.
8. Report to the user: which repo rules you wrote into the `ADAPT` sections,
   which hook branches you kept or added and in which path, and the test
   commands you ran.

## Measuring it yourself

Track three things against a normal week:

1. Cost per completed task, not tokens.
2. Whether the cheap worker handed back anything wrong, and what the rework cost.
3. How often the hook denied something, and how often you overrode it (`#full`).

## License

MIT
