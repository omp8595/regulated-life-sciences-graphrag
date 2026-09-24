from app import orchestrate


CASES = [
    ("US governed dosage", "What is the approved dosage for BRUKINSA?", "ROLE_COMMERCIAL", "INTERNAL_INSIGHT", "United States", "ANSWERED"),
    ("Promotional publication blocked", "What is the ALPINE interim publication?", "ROLE_COMMERCIAL", "PROMOTIONAL_CONTENT", "United States", "BLOCKED"),
    ("Medical efficacy evidence", "How did zanubrutinib perform compared with ibrutinib?", "ROLE_MEDICAL", "MEDICAL_RESPONSE", "Global", "EVIDENCE_ONLY"),
    ("India dosage abstains", "What is the approved dosage in India?", "ROLE_COMMERCIAL", "INTERNAL_INSIGHT", "India", "ABSTAIN"),
    ("Complex medical efficacy and safety", "How did zanubrutinib compare with ibrutinib regarding efficacy and cardiac safety?", "ROLE_MEDICAL", "MEDICAL_RESPONSE", "Global", "EVIDENCE_ONLY"),
    ("Complex promotional request blocked", "Can ALPINE efficacy and cardiac safety findings support a superiority promotion?", "ROLE_COMMERCIAL", "PROMOTIONAL_CONTENT", "United States", "BLOCKED"),
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

    complex_result = orchestrate(
        "How did zanubrutinib compare with ibrutinib regarding efficacy and cardiac safety?",
        "ROLE_MEDICAL", "MEDICAL_RESPONSE", "Global"
    )
    focuses = {item.get("focus") for item in complex_result.get("evidence_results", [])}
    if not {"EFFICACY", "SAFETY"}.issubset(focuses):
        raise SystemExit(f"Complex evidence coverage failed: {focuses}")
    print("PASS | Complex evidence covers EFFICACY and SAFETY")
    print(f"All {len(CASES)} smoke tests passed.")


if __name__ == "__main__":
    main()
