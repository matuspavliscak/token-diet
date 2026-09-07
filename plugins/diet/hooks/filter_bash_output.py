#!/usr/bin/env python3
"""PreToolUse gate on Bash: refuse ops commands that dump an unfiltered payload.

On an ops-heavy repo, command output (REST JSON, job/run JSON, test logs) can
outweigh file reads in token spend. Narrowing at the source costs nothing and
keeps the payload out of the conversation entirely.

The branches below cover the Azure CLI, the Databricks CLI, raw REST against
either, and pytest. Add or remove branches in check() to match your own stack.

Append the token #full to any command to bypass the gate.
Fails open: unexpected input or an internal error allows the call.
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


def deny(reason: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    sys.exit(0)


def has_flag(seg: str, *flags: str) -> bool:
    return any(re.search(r"(^|\s)" + re.escape(f) + r"(\s|=|$)", seg) for f in flags)


def bounded(seg: str) -> bool:
    return bool(FILTER.search(seg)) or bool(REDIRECT.search(seg))


def check(seg: str) -> None:
    # az prints the whole JSON document by default.
    if re.match(r"^(sudo\s+)?az\s", seg):
        if has_flag(seg, "--query", "-o", "--output", "--help", "-h") or bounded(seg):
            return
        deny(
            "az returns the whole JSON document by default, which lands in the "
            "conversation and is resent every turn. Narrow it at the source: add "
            '--query "<JMESPath>", or -o tsv / -o none for a write, or pipe to jq. '
            "Append #full to the command if you genuinely need the whole document."
        )

    # The databricks CLI has no --query, so it needs a downstream filter.
    if re.search(r"(^|/)databricks\s", seg) and re.search(
        r"\b(api|jobs|runs|clusters|warehouses|workspace|pipelines)\b", seg
    ):
        big = re.search(r"\b(get|list|get-run|get-status|export)\b", seg) or re.search(
            r"sql/statements", seg
        )
        if big and not (
            bounded(seg) or has_flag(seg, "--help", "-h")
        ):
            deny(
                "This databricks call returns a large JSON payload and the CLI has no "
                "--query. Pipe it: `| jq '<path>'` (run_id, state, error, task keys) or "
                "`| head -c 2000`. Append #full if you need the whole payload."
            )

    # Raw REST against ADO or Databricks.
    if re.search(r"^(sudo\s+)?curl\s", seg) and re.search(
        r"(dev\.azure\.com|azuredatabricks\.net|vssps\.visualstudio\.com)", seg
    ):
        if not bounded(seg):
            deny(
                "An ADO/Databricks REST response is mostly fields you will not read. "
                "Pipe it through jq and select the fields you need. Append #full to "
                "override."
            )

    # pytest defaults to a verbose report.
    if re.search(r"(^|[/\s])pytest\b", seg):
        if not (has_flag(seg, "-q", "--quiet", "--co", "--collect-only", "--version", "--help", "-h") or bounded(seg)):
            deny(
                "Run pytest as `-q --tb=short` (add `-x` when you only need the first "
                "failure). The default report prints one line per test plus full "
                "tracebacks. Append #full to override."
            )


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    if payload.get("tool_name") != "Bash":
        return
    command = (payload.get("tool_input") or {}).get("command") or ""
    if ESCAPE in command:
        return
    # Join backslash line continuations and pipes that wrap to the next line, so a
    # multi-line pipeline is judged as one segment and its filter is seen.
    command = re.sub(r"\\\n\s*", " ", command)
    command = re.sub(r"\|\s*\n\s*", "| ", command)
    for seg in re.split(r"&&|\|\||;|\n", command):
        seg = seg.strip()
        if seg:
            check(seg)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
