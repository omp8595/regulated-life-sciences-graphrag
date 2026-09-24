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

## Optional: launch the unified UI

```python
import os
os.environ["PLATFORM_ADMIN_KEY"] = "kaggle-demo-admin"
os.environ["GRADIO_SHARE"] = "true"

!python product_ui.py
```

Use the public Gradio URL printed by the process only for demonstration. This
repository is a technical prototype and not a validated production GxP system.
