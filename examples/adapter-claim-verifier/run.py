#!/usr/bin/env python3
"""Verify compact adapter evidence before a named host adapter claim.

This verifier is an evidence gate only. It does not implement or claim support
for Claude, Gemini, or any other named host adapter.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
RUNNER_SCRIPT = ROOT / "examples" / "adapter-contract-runner" / "run.py"
REQUIRED_TEMPLATE_PLACEHOLDERS = (
    "cadence_args",
    "case_work_dir",
    "host_event_file",
)


def load_adapter_contract_runner():
    spec = importlib.util.spec_from_file_location("adapter_contract_runner_for_claim_verifier", RUNNER_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"could not load adapter contract runner: {RUNNER_SCRIPT}")
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify compact adapter evidence before a host adapter claim.")
    parser.add_argument("--evidence-file", type=Path, required=True, help="Compact adapter-contract evidence JSON file.")
    parser.add_argument("--claim-host", help="Named non-Codex host adapter support claim to check.")
    parser.add_argument(
        "--binding-command-template",
        help="Optional proposed binding command template; when supplied, it must match the evidence file.",
    )
    return parser


def blocker(code: str, message: str) -> dict[str, str]:
    return {
        "code": code,
        "message": message,
    }


def required_placeholder_flags(evidence: dict[str, Any] | None) -> dict[str, bool]:
    checklist = evidence.get("checklist_evidence") if isinstance(evidence, dict) else None
    placeholders = checklist.get("binding_template_placeholders") if isinstance(checklist, dict) else None
    if not isinstance(placeholders, dict):
        placeholders = {}
    return {
        placeholder: placeholders.get(placeholder) is True
        for placeholder in REQUIRED_TEMPLATE_PLACEHOLDERS
    }


def compact_contract_labels(evidence: dict[str, Any] | None) -> list[str]:
    contracts = evidence.get("contracts") if isinstance(evidence, dict) else None
    if not isinstance(contracts, list):
        return []
    return [
        contract.get("label")
        for contract in contracts
        if isinstance(contract, dict) and isinstance(contract.get("label"), str)
    ]


def claim_host_value(raw_claim_host: str | None) -> str | None:
    if raw_claim_host is None:
        return None
    return raw_claim_host.strip()


def invalid_evidence_packet(
    *,
    evidence_file: Path,
    claim_host: str | None,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "result": "adapter_claim_verification_failed",
        "claim_decision": "evidence_invalid",
        "named_host_claim_allowed": False,
        "claim_host": claim_host,
        "evidence_file": str(evidence_file),
        "evidence_scope": "compact_adapter_contract_evidence",
        "binding_command_mode": None,
        "binding_command_template": None,
        "binding_command_template_matches_evidence": None,
        "required_placeholders": required_placeholder_flags(None),
        "observed_contract_labels": [],
        "blockers": [
            blocker("invalid_adapter_contract_evidence", error)
            for error in errors
        ],
        "recommended_next_action": "fix_adapter_contract_evidence",
    }


def evaluate_claim(
    *,
    evidence_file: Path,
    evidence: dict[str, Any],
    claim_host: str | None,
    supplied_binding_command_template: str | None,
) -> dict[str, Any]:
    binding_mode = evidence.get("binding_command_mode")
    evidence_template = evidence.get("binding_command_template")
    placeholders = required_placeholder_flags(evidence)
    blockers: list[dict[str, str]] = []
    template_matches_evidence = None

    if claim_host is None:
        claim_decision = "generic_only"
        named_host_claim_allowed = False
        recommended_next_action = "keep_pr_generic"
    elif not claim_host:
        blockers.append(
            blocker(
                "claim_host_missing",
                "--claim-host must name the host adapter claim being checked.",
            )
        )
        claim_decision = "must_remain_generic"
        named_host_claim_allowed = False
        recommended_next_action = "keep_pr_generic_or_run_binding_contract_with_template"
    else:
        if binding_mode != "template":
            blockers.append(
                blocker(
                    "named_claim_requires_template_binding_evidence",
                    "Named host claims require compact evidence from a --binding-command-template run.",
                )
            )
        if not isinstance(evidence_template, str) or not evidence_template.strip():
            blockers.append(
                blocker(
                    "binding_command_template_missing",
                    "Compact evidence must include the proposed binding command template.",
                )
            )
        for placeholder, observed in placeholders.items():
            if not observed:
                blockers.append(
                    blocker(
                        f"binding_template_missing_{placeholder}_placeholder",
                        f"Binding command template must include {{{placeholder}}}.",
                    )
                )
        if supplied_binding_command_template is not None:
            template_matches_evidence = supplied_binding_command_template == evidence_template
            if not template_matches_evidence:
                blockers.append(
                    blocker(
                        "binding_command_template_mismatch",
                        "Supplied --binding-command-template does not match the compact evidence file.",
                    )
                )

        if blockers:
            claim_decision = "must_remain_generic"
            named_host_claim_allowed = False
            recommended_next_action = "keep_pr_generic_or_run_binding_contract_with_template"
        else:
            claim_decision = "named_host_claim_allowed"
            named_host_claim_allowed = True
            recommended_next_action = "claim_named_host_with_mapping_and_implementation_evidence"

    return {
        "result": (
            "adapter_claim_verification_passed"
            if not blockers
            else "adapter_claim_verification_failed"
        ),
        "claim_decision": claim_decision,
        "named_host_claim_allowed": named_host_claim_allowed,
        "claim_host": claim_host,
        "evidence_file": str(evidence_file),
        "evidence_scope": "compact_adapter_contract_evidence",
        "binding_command_mode": binding_mode,
        "binding_command_template": evidence_template,
        "binding_command_template_matches_evidence": template_matches_evidence,
        "required_placeholders": placeholders,
        "observed_contract_labels": compact_contract_labels(evidence),
        "blockers": blockers,
        "recommended_next_action": recommended_next_action,
    }


def verify_claim(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    runner = load_adapter_contract_runner()
    claim_host = claim_host_value(args.claim_host)
    evidence, load_errors = runner.load_compact_evidence_file(args.evidence_file)
    if evidence is None:
        return 1, invalid_evidence_packet(
            evidence_file=args.evidence_file,
            claim_host=claim_host,
            errors=load_errors,
        )

    schema_errors = runner.validate_compact_evidence_against_schema(evidence)
    if schema_errors:
        return 1, invalid_evidence_packet(
            evidence_file=args.evidence_file,
            claim_host=claim_host,
            errors=schema_errors,
        )

    packet = evaluate_claim(
        evidence_file=args.evidence_file,
        evidence=evidence,
        claim_host=claim_host,
        supplied_binding_command_template=args.binding_command_template,
    )
    return (0 if packet["result"] == "adapter_claim_verification_passed" else 1), packet


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    exit_code, packet = verify_claim(args)
    print(json.dumps(packet, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
