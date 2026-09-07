# token-diet

Two-model routing for Claude Code: cheap models do the text moving, the
expensive model only sees the result.

Inspired by [Portal by Spotify cut my Claude Code token usage by 90%](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90).
Their 90% assumes files are resent at full price every turn; with prompt
caching, resends are already cheap. What this buys is context room on long
sessions, and not paying frontier rates for mechanical work.

## What's in it

- **`bulk-reader`** (Haiku subagent) - reads large files, returns bullets with
  `file:line` anchors. The file itself never enters the main session.
- **`code-writer`** (Haiku subagent) - writes repetitive code (tests that
  mirror a neighbouring module, config resources, scaffolding) from a spec plus
  a required reference file, straight to disk. Refuses to work without a
  reference.
- **A PreToolUse hook** (`filter_bash_output.py`) - refuses shell commands
  that would dump an unfiltered payload into the session: `az` without
  `--query`, Databricks CLI get/list without a downstream filter, raw
  ADO/Databricks REST without `jq`, `pytest` without `-q`. The refusal message
  contains the fix. Append `#full` to any command to bypass. Fails open on
  internal errors.

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
5. Adapt the hook: keep only the `check()` branches for CLIs the repo actually
   uses, and add branches for its own payload-heavy commands. Test it without
   burning a session:

   ```
   echo '{"tool_name":"Bash","tool_input":{"command":"pytest tests/"}}' \
     | python3 .claude/hooks/filter_bash_output.py
   ```

   A deny prints JSON with a `permissionDecision` of `deny`; an allow prints
   nothing. Also verify the escape hatch: the same command with ` #full`
   appended must print nothing.
6. Tell the user to restart their Claude Code session - hooks load at startup.
7. Report to the user: which repo rules you wrote into the `ADAPT` sections,
   which hook branches you kept or added, and the test commands you ran.

Usage guidance for the main-session model, once installed: delegate reads of
files over ~300 lines to `bulk-reader` when you need answers, not the text;
delegate mirror-pattern code (a test like the twenty tests next to it) to
`code-writer` with an explicit reference file; keep design, debugging, and
anything you will edit in the main session.

## Measuring

If you try it, track three things and compare against a normal week:

1. How far sessions get before the context fills.
2. Whether the cheap worker handed back anything wrong.
3. How often the hook got in your way, and how often you overrode it (`#full`).

Known cost: false positives. `pytest --version` was denied on day one and cost
one retry before it was allowlisted. The deny-with-fix message keeps each
incident to a single retry.

## License

MIT
