# Kaggle validation

This is the shortest end-to-end validation path for the governed Pharma Semantic
Layer.

## 1. Create a new Kaggle Notebook

Use a Python notebook and enable **Internet** in notebook Settings. The test
clones the public GitHub branch and installs its pinned requirements.

## 2. Clone the feature branch

```python
!git clone -b feat/pharma-semantic-layer-v1 --single-branch \
  https://github.com/omp8595/regulated-life-sciences-graphrag.git
%cd regulated-life-sciences-graphrag
```

## 3. Install dependencies

```python
!pip install -q -r requirements.txt
```

If Kaggle asks for a runtime restart after dependency changes, restart the
session and run the clone/`%cd` cell again before continuing.

## 4. Run the full Product API regression suite

```python
!python -m unittest product_api.test_product_api
```

Expected result: all tests pass.

## 5. Run the Kaggle semantic smoke test

```python
!python kaggle_semantic_smoke.py
```

The smoke test verifies, in one run:

1. tenant creation;
2. evidence upload and quarantine;
3. ingestion and semantic graph indexing;
4. SME-review boundary enforcement;
5. BRUKINSA → zanubrutinib semantic retrieval with zero exact lexical overlap;
6. semantic catalog and external trial mapping;
7. governed alias-change proposal;
8. self-approval prevention;
9. independent Regulatory approval;
10. semantic concept version 1 → SUPERSEDED and version 2 → ACTIVE;
11. resolution of the newly approved alias;
12. hash-linked audit-chain verification.

The final line should be:

```text
ALL KAGGLE SMOKE TESTS PASSED
```

## 6. Test the full Gradio UI

Use one shared database for the bootstrap script and the UI process:

```python
%env PRODUCT_DATA_DIR=/kaggle/working/regulated_graphrag_ui
%env PRODUCT_DB_PATH=/kaggle/working/regulated_graphrag_ui/product.db
%env PLATFORM_ADMIN_KEY=kaggle-demo-admin
%env GRADIO_SHARE=true
```

Provision a demo tenant plus role-specific API keys:

```python
!python kaggle_ui_bootstrap.py
```

Copy the printed keys. You will use different keys in the same **API key**
field to simulate the regulated roles.

Launch the UI:

```python
!python product_ui.py
```

Open the public Gradio URL printed by the process.

### UI validation sequence

1. Paste the `ROLE_MEDICAL` key.
2. **Document ingestion** → upload a text file containing ALPINE /
   zanubrutinib evidence.
3. **Governed query** → ask `BRUKINSA`. Confirm the result is
   `EVIDENCE_ONLY` and shows semantic related match `zanubrutinib`.
4. **Semantic catalog** → refresh and inspect BRUKINSA, zanubrutinib and ALPINE.
5. Replace the key with the `ROLE_SEMANTIC_STEWARD` key.
6. **Semantic governance** → propose `ADD_ALIAS` for
   `BRAND:BRUKINSA`, for example `Brukinsa oncology brand`.
7. Try approving with the same Steward key. It should be blocked because
   self-approval is prohibited.
8. Replace the API key with the `ROLE_REGULATORY` key.
9. Refresh the semantic change queue and approve the pending request with
   authorization confirmation checked.
10. Return to **Semantic catalog** and confirm BRUKINSA is on the next active
    semantic version and the new alias appears.
11. Use **Governance dashboard** to confirm the tenant audit chain remains valid.

The UI also contains the existing **SME validation** and **MLR review** tabs, so
the same notebook can demonstrate the complete regulated lifecycle.

Use the public Gradio URL only for demonstration. This repository is a technical
prototype and not a validated production GxP system.
