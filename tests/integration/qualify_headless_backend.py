"""Run three consecutive executable-level backend qualification rounds."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time


def run_step(name: str, command: list[str], directory: Path) -> dict:
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True)
    (directory / f"{name}.stdout.log").write_text(result.stdout, encoding="utf-8")
    (directory / f"{name}.stderr.log").write_text(result.stderr, encoding="utf-8")
    return {
        "name": name,
        "status": "PASS" if result.returncode == 0 else "FAIL",
        "returncode": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 3),
        "command": command,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.rounds <= 20:
        parser.error("--rounds must be 1..20")
    executable = args.exe.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    script_dir = Path(__file__).resolve().parent
    all_rounds = []
    for number in range(1, args.rounds + 1):
        round_dir = output / f"round-{number}"
        round_dir.mkdir()
        steps = [
            run_step("transport-smoke", [
                sys.executable, str(script_dir / "frozen_transport_smoke.py"),
                "--exe", str(executable),
            ], round_dir),
            run_step("negative-matrix", [
                sys.executable, str(script_dir / "structured_tls_negative_matrix.py"),
                "--exe", str(executable), "--output", str(round_dir / "negative-cases"),
            ], round_dir),
            run_step("rfc8071-heartbeat", [
                sys.executable, str(script_dir / "rfc8071_heartbeat_probe.py"),
                "--exe", str(executable), "--pcap", str(round_dir / "heartbeat.pcap"),
                "--report", str(round_dir / "heartbeat.json"),
            ], round_dir),
        ]
        status = "PASS" if all(step["status"] == "PASS" for step in steps) else "FAIL"
        row = {"round": number, "status": status, "steps": steps}
        all_rounds.append(row)
        print(f"Round {number}/{args.rounds}: {status}", flush=True)
        if status != "PASS":
            break
    passed = sum(row["status"] == "PASS" for row in all_rounds)
    report = {
        "version": 1,
        "executable": str(executable),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "required_consecutive_rounds": args.rounds,
        "completed_rounds": len(all_rounds),
        "passed_rounds": passed,
        "rounds": all_rounds,
        "status": "PASS" if passed == args.rounds else "FAIL",
        "scope": (
            "Executable transport, structured TLS negative classification and RFC 8071 "
            "ClientHello qualification; this is not a DUT or O-RAN conformance verdict"
        ),
    }
    (output / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in (
        "status", "passed_rounds", "required_consecutive_rounds", "executable"
    )}, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
