#!/usr/bin/env python3
"""
production_check.py

CLI Production Readiness Check for Ashwani Agent Company.

Usage:
    python production_check.py

Exit codes:
    0 = PASS
    1 = WARNING
    2 = FAIL
"""

import sys
import os

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from control.production_validator import ProductionValidator


COLORS = {
    "RESET":   "\033[0m",
    "BOLD":    "\033[1m",
    "GREEN":   "\033[92m",
    "YELLOW":  "\033[93m",
    "RED":     "\033[91m",
    "CYAN":    "\033[96m",
    "WHITE":   "\033[97m",
    "DIM":     "\033[2m",
}


def colorize(text: str, color: str) -> str:
    if sys.stdout.isatty() or os.environ.get("FORCE_COLOR"):
        return f"{COLORS.get(color, '')}{text}{COLORS['RESET']}"
    return text


def severity_color(severity: str) -> str:
    return {"PASS": "GREEN", "WARNING": "YELLOW", "FAIL": "RED"}.get(severity, "WHITE")


def status_icon(status: str) -> str:
    return {"PASS": "PASS", "WARNING": "WARN", "FAIL": "FAIL"}.get(status, "?")


def print_banner():
    sep = "=" * 54
    print(colorize(sep, "CYAN"))
    print(colorize("  ASHWANI AGENT COMPANY", "BOLD"))
    print(colorize("  Production Readiness Report", "WHITE"))
    print(colorize(sep, "CYAN"))
    print()


def print_overall(report: dict):
    score  = report["overall_score"]
    status = report["overall_status"]
    color  = severity_color(status)
    icon   = status_icon(status)

    print(colorize("  OVERALL SCORE", "BOLD"))
    print()
    score_bar = "#" * (score // 5) + "." * (20 - score // 5)
    print(f"  [{score_bar}] {colorize(f'{score}%', color)}  {colorize(f'{icon} {status}', color)}")
    print()


def print_section_table(report: dict):
    section_scores = report["section_scores"]
    print(colorize("  SECTION RESULTS", "BOLD"))
    print()
    print(f"  {'Section':<22} {'Score':>6}  {'Status':<10}  {'Findings'}")
    print(f"  {'-'*22} {'-'*6}  {'-'*10}  {'-'*30}")

    for section, data in section_scores.items():
        score  = data["score"]
        status = data["status"]
        color  = severity_color(status)
        findings = data["findings"]
        pass_count = sum(1 for f in findings if f["severity"] == "PASS")
        warn_count = sum(1 for f in findings if f["severity"] == "WARNING")
        fail_count = sum(1 for f in findings if f["severity"] == "FAIL")
        finding_summary = f"[OK:{pass_count} WN:{warn_count} FL:{fail_count}]"

        print(f"  {section:<22} {score:>5}%  {colorize(status[:10], color):<20}  {finding_summary}")
    print()


def print_recommendations(report: dict):
    section_scores = report["section_scores"]
    recommendations = []

    for section, data in section_scores.items():
        for finding in data["findings"]:
            if finding["severity"] in ("FAIL", "WARNING"):
                recommendations.append((finding["severity"], section, finding["message"], finding["recommendation"]))

    if not recommendations:
        print(colorize("  [OK] No recommendations -- system is production ready!", "GREEN"))
        print()
        return

    # Sort: FAIL first, then WARNING
    recommendations.sort(key=lambda x: (0 if x[0] == "FAIL" else 1, x[1]))

    print(colorize("  RECOMMENDATIONS", "BOLD"))
    print()
    for severity, section, message, rec in recommendations:
        color = severity_color(severity)
        label = "[FAIL]" if severity == "FAIL" else "[WARN]"
        print(f"  {colorize(label, color)} [{section}] {message}")
        print(f"    -> {rec}")
        print()


def main() -> int:
    print_banner()

    validator = ProductionValidator()
    report    = validator.run()

    print_overall(report)
    print_section_table(report)

    sep = "=" * 54
    print(colorize(sep, "CYAN"))
    print()

    print_recommendations(report)

    print(colorize(sep, "CYAN"))

    status = report["overall_status"]
    return {"PASS": 0, "WARNING": 1, "FAIL": 2}.get(status, 2)


if __name__ == "__main__":
    sys.exit(main())
