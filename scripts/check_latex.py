#!/usr/bin/env python3
"""Validate a full LaTeX build and compare quality issues with its baseline."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


SEMANTIC_ENVIRONMENTS = (
    "definition", "theorem", "lemma", "proposition", "corollary",
    "proof", "example", "exercise",
)


def hard_issues(text: str) -> set[str]:
    patterns = (
        r"^! .+$",
        r"LaTeX Error: .+$",
        r"Package .+ Error: .+$",
        r"Undefined control sequence",
        r"There were undefined references",
        r"Reference [`'].+? undefined",
        r"Citation [`'].+? undefined",
        r"Label [`'].+? multiply defined",
        r"There were multiply-defined labels",
        r"Biber error: .+$",
        r"Emergency stop",
        r"File ended while scanning use of",
        r"Missing \$ inserted",
        r"Extra \}, or forgotten \$",
    )
    found: set[str] = set()
    for pattern in patterns:
        found.update(re.findall(pattern, text, flags=re.MULTILINE | re.IGNORECASE))
    return found


def quality_issues(text: str) -> set[str]:
    found = set(re.findall(r"^Missing character: .+$", text, flags=re.MULTILINE))
    for match in re.finditer(r"Overfull \\hbox \(([0-9.]+)pt too wide\)", text):
        if float(match.group(1)) >= 5.0:
            found.add(match.group(0))
    found.update(re.findall(r"(?:LaTeX|Package .*?) Warning: Font shape .+$", text, flags=re.MULTILINE))
    return found


def managed_timestamp_issues(root: Path) -> list[str]:
    issues = []
    files = list((root / "chapters").glob("*.tex")) + list((root / "lectures").glob("*/*.tex"))
    timestamp = re.compile(r"\d{2}:\d{2}:\d{2}(?:\.\d{3})?--\d{2}:\d{2}:\d{2}(?:\.\d{3})?")
    for path in files:
        text = path.read_text(encoding="utf-8")
        if "% video-notes:managed" not in text:
            continue
        for environment in SEMANTIC_ENVIRONMENTS:
            pattern = re.compile(
                rf"\\begin\{{{environment}\}}(?P<body>.*?)\\end\{{{environment}\}}",
                flags=re.DOTALL,
            )
            for match in pattern.finditer(text):
                if not timestamp.search(match.group("body")):
                    line = text.count("\n", 0, match.start()) + 1
                    issues.append(f"{path.relative_to(root)}:{line}: {environment} lacks a video timestamp range")
    return issues


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--log", type=Path)
    parser.add_argument("--baseline-log", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    log = args.log or root / "elegantbook-cn.log"
    if not log.is_file():
        print(f"LaTeX validation failed: missing build log {log}", file=sys.stderr)
        return 1
    text = log.read_text(encoding="utf-8", errors="replace")
    errors = sorted(hard_issues(text)) + managed_timestamp_issues(root)
    current_quality = quality_issues(text)
    baseline_quality: set[str] = set()
    if args.baseline_log:
        if not args.baseline_log.is_file():
            errors.append(f"missing baseline log: {args.baseline_log}")
        else:
            baseline_text = args.baseline_log.read_text(encoding="utf-8", errors="replace")
            baseline_hard = hard_issues(baseline_text)
            if baseline_hard:
                errors.append("baseline log contains hard LaTeX errors")
            baseline_quality = quality_issues(baseline_text)
    new_quality = sorted(current_quality - baseline_quality) if args.baseline_log else []
    if new_quality:
        errors.extend(f"new quality issue: {issue}" for issue in new_quality)
    if errors:
        print("LaTeX validation failed:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    if current_quality:
        qualifier = "pre-existing baseline" if args.baseline_log else "reported warning"
        print(f"LaTeX quality review: {len(current_quality)} {qualifier} issue(s); no new blocking issue.")
    print("LaTeX validation passed: no hard errors, unresolved references, duplicate labels, or new quality regressions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
