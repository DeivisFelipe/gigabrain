"""Command line of the validation layer, for envelope files.

    python -m gigabrain.validation.cli gigabrain/validation/examples/good.json
    python -m gigabrain.validation.cli input.json --format markdown
    python -m gigabrain.validation.cli input.json --policy strict --save-to reports.jsonl
    python -m gigabrain.validation.cli --list-checks

To validate what is in the Requirements Repository, use `python main.py validate`.

`-` reads the envelope from standard input.

Exit code: 0 when approved (with or without remarks), 1 when rejected, 2 when
the input is invalid. That lets validation be used as a gate in a script or in CI.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters.jsonl_store import JsonlReportStore
from .engine import ValidationEngine
from .envelope import EnvelopeError
from .envelope import from_json as envelope_from_json
from .model import Verdict
from .policy import POLICIES
from .registry import REGISTRY, CheckConfig
from .report import to_agent_feedback, to_console, to_json, to_markdown

EXIT_OK = 0
EXIT_REJECTED = 1
EXIT_BAD_INPUT = 2

RENDERERS = {
    "console": to_console,
    "markdown": to_markdown,
    "json": to_json,
    "feedback": to_agent_feedback,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m gigabrain.validation.cli",
        description="Validate requirement artefacts produced by the GigaBrain agents (envelope files).",
    )
    parser.add_argument("input", nargs="?", help="envelope file (.json); '-' reads from standard input")
    parser.add_argument("--format", choices=tuple(RENDERERS), default="console", help="report format (default: console)")
    parser.add_argument("--policy", choices=tuple(POLICIES), default="default", help="ruler of the gate (default: default)")
    parser.add_argument("--disable", metavar="CHECK_ID", action="append", default=[],
                        help="switch a check off (may be repeated); used for ablation")
    parser.add_argument("--no-required-source", action="store_true",
                        help="do not require the artefact to declare a source (to measure the raw output of the agents)")
    parser.add_argument("--save-to", metavar="FILE.jsonl", help="append the report to a JSONL file")
    parser.add_argument("--output", metavar="FILE", help="write the report to a file instead of standard output")
    parser.add_argument("--list-checks", action="store_true", help="list the registered checks and exit")
    return parser


def list_checks() -> str:
    lines = [f"{len(REGISTRY)} checks registered"]
    current_level = None
    current_dimension = None
    for check in REGISTRY.all():
        if check.level != current_level:
            current_level = check.level
            current_dimension = None
            lines.append("")
            lines.append(f"== level: {current_level.value} ==")
        if check.dimension != current_dimension:
            current_dimension = check.dimension
            lines.append("")
            lines.append(f"  [{current_dimension.label}]")
        lines.append(f"    {check.id:<38} {check.default_severity.value:<8} weight {check.weight}")
        lines.append(f"      {check.description}")
        if check.requires:
            lines.append(f"      when: {check.requires}")
    return "\n".join(lines)


def _read_input(path_arg: str) -> str:
    if path_arg == "-":
        return sys.stdin.read()
    path = Path(path_arg)
    if not path.exists():
        raise EnvelopeError(f"File not found: {path_arg}")
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_checks:
        print(list_checks())
        return EXIT_OK

    if not args.input:
        parser.print_help()
        return EXIT_BAD_INPUT

    try:
        envelope = envelope_from_json(_read_input(args.input))
    except EnvelopeError as exc:
        print(f"error in the input: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    config = CheckConfig(
        disabled_checks=frozenset(args.disable),
        require_evidence=not args.no_required_source,
    )
    report = ValidationEngine(config=config, policy=POLICIES[args.policy]).validate(envelope)
    output = RENDERERS[args.format](report)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
        print(f"report written to {args.output}", file=sys.stderr)
    else:
        print(output)

    if args.save_to:
        JsonlReportStore(args.save_to).save(report)
        print(f"report appended to {args.save_to}", file=sys.stderr)

    return EXIT_REJECTED if report.verdict is Verdict.REJECTED else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
