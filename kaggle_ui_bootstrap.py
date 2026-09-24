from __future__ import annotations

import os

from fastapi.testclient import TestClient

from product_api.app import app


TENANT_ID = os.getenv("KAGGLE_UI_TENANT", "kaggle_ui_demo")
TENANT_NAME = os.getenv("KAGGLE_UI_TENANT_NAME", "Kaggle UI Demo Pharma")
ADMIN_KEY = os.getenv("PLATFORM_ADMIN_KEY", "")

ROLES = (
    ("medical_user", "ROLE_MEDICAL"),
    ("semantic_steward", "ROLE_SEMANTIC_STEWARD"),
    ("regulatory_reviewer", "ROLE_REGULATORY"),
    ("mlr_reviewer", "ROLE_MLR_REVIEWER"),
    ("commercial_user", "ROLE_COMMERCIAL"),
)


def main() -> None:
    if not ADMIN_KEY:
        raise RuntimeError(
            "PLATFORM_ADMIN_KEY is required. In Kaggle set it before running this script."
        )

    print("\n=== Kaggle UI bootstrap ===\n")
    with TestClient(app) as client:
        tenant = client.post(
            "/v1/tenants",
            json={"tenant_id": TENANT_ID, "name": TENANT_NAME},
        )
        if tenant.status_code not in {201, 409}:
            raise RuntimeError(f"Tenant setup failed: {tenant.status_code} {tenant.text}")

        print(f"Tenant: {TENANT_ID}")
        print("\nCopy these demo API keys into the UI as you switch roles:\n")

        for actor_id, role in ROLES:
            issued = client.post(
                "/v1/auth/api-keys",
                headers={"X-Platform-Admin-Key": ADMIN_KEY},
                json={"tenant_id": TENANT_ID, "actor_id": actor_id, "role": role},
            )
            if issued.status_code != 201:
                raise RuntimeError(
                    f"API key provisioning failed for {role}: "
                    f"{issued.status_code} {issued.text}"
                )
            print(f"{role:24} {issued.json()['api_key']}")

    print(
        "\nUse ROLE_MEDICAL for ingestion/query/SME, "
        "ROLE_SEMANTIC_STEWARD to propose a semantic change, "
        "ROLE_REGULATORY to approve it, ROLE_MLR_REVIEWER for MLR, "
        "and ROLE_COMMERCIAL to test promotional blocking/approval."
    )


if __name__ == "__main__":
    main()
