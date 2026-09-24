from app import orchestrate


CASES = [
    ("US governed dosage", "What is the approved dosage for BRUKINSA?", "ROLE_COMMERCIAL", "INTERNAL_INSIGHT", "United States", "ANSWERED"),
    ("Promotional publication blocked", "What is the ALPINE interim publication?", "ROLE_COMMERCIAL", "PROMOTIONAL_CONTENT", "United States", "BLOCKED"),
    ("Medical efficacy evidence", "How did zanubrutinib perform compared with ibrutinib?", "ROLE_MEDICAL", "MEDICAL_RESPONSE", "Global", "EVIDENCE_ONLY"),
    ("India dosage abstains", "What is the approved dosage in India?", "ROLE_COMMERCIAL", "INTERNAL_INSIGHT", "India", "ABSTAIN"),
]


def main() -> None:
    failures = []
    for name, question, role, purpose, market, expected in CASES:
        result = orchestrate(question, role, purpose, market)
        actual = result["status"]
        print(f"{'PASS' if actual == expected else 'FAIL'} | {name} | expected={expected} actual={actual}")
        if actual != expected:
            failures.append((name, expected, actual))
    if failures:
        raise SystemExit(f"Smoke tests failed: {failures}")
    print(f"All {len(CASES)} smoke tests passed.")


if __name__ == "__main__":
    main()
