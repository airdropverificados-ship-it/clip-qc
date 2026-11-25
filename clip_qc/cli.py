"""Command-line interface for clip-qc."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .core import ClipReport, check_clip

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv")


def _add_check_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--min-duration", type=float, default=None,
                        metavar="S",
                        help="fail if the clip is shorter than S seconds")
    parser.add_argument("--max-duration", type=float, default=None,
                        metavar="S",
                        help="fail if the clip is longer than S seconds")
    parser.add_argument("--expect-audio", action="store_true",
                        help="fail if the clip has no audio stream")
    parser.add_argument("--max-silence", type=float, default=None,
                        metavar="S",
                        help="fail if leading or trailing silence exceeds "
                             "S seconds")
    parser.add_argument("--json", action="store_true",
                        help="print a machine-readable JSON report")


def _print_text_report(report: ClipReport) -> None:
    print(f"{report.path}:")
    for check in report.checks:
        line = f"  {check.status:4}  {check.name}"
        if check.detail:
            line += f" — {check.detail}"
        print(line)
    print(f"  RESULT: {'PASS' if report.passed else 'FAIL'}")


def _print_json_report(reports: list[ClipReport]) -> None:
    if len(reports) == 1:
        print(json.dumps(reports[0].as_dict(), indent=2))
    else:
        passed = sum(1 for r in reports if r.passed)
        print(json.dumps({
            "files": [r.as_dict() for r in reports],
            "passed": passed,
            "failed": len(reports) - passed,
            "total": len(reports),
        }, indent=2))


def _cmd_check(args: argparse.Namespace) -> int:
    report = check_clip(
        args.clip,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        expect_audio=args.expect_audio,
        max_silence=args.max_silence,
    )
    if args.json:
        _print_json_report([report])
    else:
        _print_text_report(report)
    return 0 if report.passed else 1


def _cmd_check_all(args: argparse.Namespace) -> int:
    if not os.path.isdir(args.directory):
        print(f"error: not a directory: {args.directory}", file=sys.stderr)
        return 2
    names = sorted(
        name for name in os.listdir(args.directory)
        if name.lower().endswith(VIDEO_EXTENSIONS)
    )
    if not names:
        print(f"no video files found in {args.directory}")
        return 0
    reports = []
    for name in names:
        path = os.path.join(args.directory, name)
        reports.append(check_clip(
            path,
            min_duration=args.min_duration,
            max_duration=args.max_duration,
            expect_audio=args.expect_audio,
            max_silence=args.max_silence,
        ))
    if args.json:
        _print_json_report(reports)
    else:
        for report in reports:
            n_pass = sum(1 for c in report.checks if c.status == "PASS")
            n_fail = sum(1 for c in report.checks if c.status == "FAIL")
            status = "PASS" if report.passed else "FAIL"
            print(f"{status}  {report.path} "
                  f"({n_pass} passed, {n_fail} failed)")
        passed = sum(1 for r in reports if r.passed)
        print(f"{passed} passed, {len(reports) - passed} failed, "
              f"{len(reports)} total")
    return 0 if all(r.passed for r in reports) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clip-qc",
        description="Check video clips before you publish them.",
    )
    parser.add_argument("--version", action="version",
                        version=f"clip-qc {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check", help="check a single clip")
    p_check.add_argument("clip", help="path to the video clip")
    _add_check_options(p_check)
    p_check.set_defaults(func=_cmd_check)

    p_all = sub.add_parser(
        "check-all", help="check every clip in a directory")
    p_all.add_argument("directory", help="directory with video clips")
    _add_check_options(p_all)
    p_all.set_defaults(func=_cmd_check_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
