#!/usr/bin/env python3
"""PreToolUse gate on Bash: keep unfiltered command payloads out of the session.

On an ops-heavy repo, command output (REST JSON, job/run JSON, test logs) can
outweigh file reads in token spend. Narrowing at the source costs nothing and
keeps the payload out of the conversation entirely.

Two mechanisms, picked per command by one rule: can the fix be applied without
deciding anything on the model's behalf?

  REWRITE (permissionDecision "allow" + updatedInput) when the narrowing is
  mechanical and loses nothing the caller wanted - pytest's report flags. The
  model never sees a refusal, so there is no retry and no correction loop.

  DENY (with the fix in the message) when narrowing needs a decision the hook
  cannot make - which JMESPath, which jq path. Truncating those instead would
  trade a visible retry for a silent omission, which is the worse failure.

Append #full to any command to bypass both.
Fails open: unexpected input or an internal error allows the call unchanged.
"""

import json
import re
import sys

ESCAPE = "#full"

# A downstream consumer that bounds or extracts, rather than echoing everything.
FILTER = re.compile(
    r"\|\s*(jq|yq|grep|rg|head|tail|wc|sed|awk|cut|tr|sort|uniq|python3?|xargs)\b"
)
REDIRECT = re.compile(r">\s*\S")

PYTEST_FLAGS = " -q --tb=short"


def emit(payload: dict) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **payload}}))
    sys.exit(0)


def deny(reason: str) -> None:
    emit({"permissionDecision": "deny", "permissionDecisionReason": reason})


def has_flag(seg: str, *flags: str) -> bool:
    return any(re.search(r"(^|\s)" + re.escape(f) + r"(\s|=|$)", seg) for f in flags)


def bounded(seg: str) -> bool:
    return bool(FILTER.search(seg)) or bool(REDIRECT.search(seg))


def needs_pytest_flags(seg: str) -> bool:
    if not re.search(r"(^|[/\s])pytest\b", seg):
        return False
    if has_flag(seg, "-q", "--quiet", "--co", "--collect-only", "--version", "--help", "-h"):
        return False
    return not bounded(seg)


def check_deny(seg: str) -> None:
    # az prints the whole JSON document by default. Which fields matter is the
    # caller's call, so this one asks rather than guesses.
    if re.match(r"^(sudo\s+)?az\s", seg):
        if has_flag(seg, "--query", "-o", "--output", "--help", "-h") or bounded(seg):
            return
        deny(
            "az returns the whole JSON document by default, which lands in the "
            "conversation and is re-sent on every later request. Narrow it at the "
            'source: add --query "<JMESPath>", or -o tsv / -o none for a write, or '
            "pipe to jq. Append #full if you need the whole document."
        )

    # The databricks CLI has no --query, so it needs a downstream filter.
    if re.search(r"(^|/)databricks\s", seg) and re.search(
        r"\b(api|jobs|runs|clusters|warehouses|workspace|pipelines)\b", seg
    ):
        big = re.search(r"\b(get|list|get-run|get-status|export)\b", seg) or re.search(
            r"sql/statements", seg
        )
        if big and not (bounded(seg) or has_flag(seg, "--help", "-h")):
            deny(
                "This databricks call returns a large JSON payload and the CLI has no "
                "--query. Pipe it: `| jq '<path>'` (run_id, state, error, task keys) or "
                "`| head -c 2000`. Append #full if you need the whole payload."
            )

    # Raw REST against a cloud control plane.
    if re.search(r"^(sudo\s+)?curl\s", seg) and re.search(
        r"(dev\.azure\.com|azuredatabricks\.net|vssps\.visualstudio\.com)", seg
    ):
        if not bounded(seg):
            deny(
                "A REST response from these hosts is mostly fields you will not read. "
                "Pipe it through jq and select the fields you need. Append #full to "
                "override."
            )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if payload.get("tool_name") != "Bash":
        return
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not command or ESCAPE in command:
        return

    # Join backslash line continuations and pipes that wrap to the next line, so a
    # multi-line pipeline is judged as one segment and its filter is seen.
    joined = re.sub(r"\\\n\s*", " ", command)
    joined = re.sub(r"\|\s*\n\s*", "| ", joined)
    segments = [s.strip() for s in re.split(r"&&|\|\||;|\n", joined)]

    # Rewrites first: a command that can be fixed in place is never denied.
    rewritten = command
    changed = False
    for seg in segments:
        if seg and needs_pytest_flags(seg):
            # Append to the pytest invocation itself, not the end of the pipeline.
            pattern = re.escape(seg)
            rewritten = re.sub(pattern, seg + PYTEST_FLAGS, rewritten, count=1)
            changed = True
    if changed:
        emit(
            {
                "permissionDecision": "allow",
                "updatedInput": {**tool_input, "command": rewritten},
            }
        )

    for seg in segments:
        if seg:
            check_deny(seg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
