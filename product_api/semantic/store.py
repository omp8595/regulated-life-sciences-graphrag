from __future__ import annotations

import re
import sqlite3
import uuid

from product_api.semantic.registry import CONCEPTS, RELATIONSHIPS, ResolvedConcept


BOOTSTRAP_EXTERNAL_MAPPINGS = (
    (
        "DRUG:ZANUBRUTINIB",
        1,
        "RXNORM",
        "2262435",
        "https://rxnav.nlm.nih.gov/id/rxnorm/2262435",
    ),
    (
        "TRIAL:ALPINE",
        1,
        "CLINICALTRIALS.GOV",
        "NCT03734016",
        "https://clinicaltrials.gov/study/NCT03734016",
    ),
    (
        "INDICATION:CLL",
        1,
        "MESH",
        "D015451",
        "https://id.nlm.nih.gov/mesh/D015451",
    ),
)


def normalize_alias(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def seed_semantic_master(conn: sqlite3.Connection, created_at_utc: str) -> None:
    for concept in CONCEPTS:
        conn.execute(
            """INSERT OR IGNORE INTO semantic_concepts
               (concept_id, version, concept_type, canonical_name, semantic_version,
                status, created_at_utc)
               VALUES (?, 1, ?, ?, 'pharma-v2', 'ACTIVE', ?)""",
            (concept.concept_id, concept.concept_type, concept.canonical_name, created_at_utc),
        )
        for alias in concept.aliases:
            conn.execute(
                """INSERT OR IGNORE INTO semantic_aliases
                   (alias_id, concept_id, concept_version, alias, normalized_alias,
                    source, confidence, status, created_at_utc)
                   VALUES (?, ?, 1, ?, ?, 'CURATED_BOOTSTRAP', 1.0, 'ACTIVE', ?)""",
                (
                    f"ALS_{uuid.uuid5(uuid.NAMESPACE_URL, concept.concept_id + ':' + normalize_alias(alias)).hex[:12].upper()}",
                    concept.concept_id,
                    alias,
                    normalize_alias(alias),
                    created_at_utc,
                ),
            )

    for relation in RELATIONSHIPS:
        relationship_key = f"{relation.source_id}:{relation.relationship}:{relation.target_id}"
        conn.execute(
            """INSERT OR IGNORE INTO semantic_relationships
               (relationship_id, source_concept_id, source_version, relationship_type,
                target_concept_id, target_version, semantic_version, status, created_at_utc)
               VALUES (?, ?, 1, ?, ?, 1, 'pharma-v2', 'ACTIVE', ?)""",
            (
                f"SEMREL_{uuid.uuid5(uuid.NAMESPACE_URL, relationship_key).hex[:12].upper()}",
                relation.source_id,
                relation.relationship,
                relation.target_id,
                created_at_utc,
            ),
        )

    for concept_id, concept_version, system, identifier, source_uri in BOOTSTRAP_EXTERNAL_MAPPINGS:
        mapping_key = f"{system}:{identifier}:{concept_id}:{concept_version}"
        conn.execute(
            """INSERT OR IGNORE INTO semantic_external_mappings
               (mapping_id, concept_id, concept_version, system, identifier,
                source_uri, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)""",
            (
                f"MAP_{uuid.uuid5(uuid.NAMESPACE_URL, mapping_key).hex[:12].upper()}",
                concept_id,
                concept_version,
                system,
                identifier,
                source_uri,
                created_at_utc,
            ),
        )


def _active_concept_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT c.*
           FROM semantic_concepts c
           JOIN (
               SELECT concept_id, MAX(version) AS version
               FROM semantic_concepts
               WHERE status='ACTIVE'
               GROUP BY concept_id
           ) latest
             ON latest.concept_id=c.concept_id AND latest.version=c.version
           WHERE c.status='ACTIVE'"""
    ).fetchall()


def get_concept(conn: sqlite3.Connection, concept_id: str) -> dict | None:
    row = conn.execute(
        """SELECT * FROM semantic_concepts
           WHERE concept_id=? AND status='ACTIVE'
           ORDER BY version DESC LIMIT 1""",
        (concept_id,),
    ).fetchone()
    return dict(row) if row else None


def list_semantic_concepts(conn: sqlite3.Connection) -> list[dict]:
    rows = _active_concept_rows(conn)
    result = []
    for row in sorted(rows, key=lambda item: (item["concept_type"], item["canonical_name"])):
        aliases = conn.execute(
            """SELECT alias, source, confidence
               FROM semantic_aliases
               WHERE concept_id=? AND concept_version=? AND status='ACTIVE'
               ORDER BY alias""",
            (row["concept_id"], row["version"]),
        ).fetchall()
        mappings = conn.execute(
            """SELECT system, identifier, source_uri
               FROM semantic_external_mappings
               WHERE concept_id=? AND concept_version=? AND status='ACTIVE'
               ORDER BY system, identifier""",
            (row["concept_id"], row["version"]),
        ).fetchall()
        result.append(
            {
                **dict(row),
                "aliases": [dict(alias) for alias in aliases],
                "external_mappings": [dict(mapping) for mapping in mappings],
            }
        )
    return result


def resolve_mentions(conn: sqlite3.Connection, text: str) -> tuple[ResolvedConcept, ...]:
    lowered = text.lower()
    rows = conn.execute(
        """SELECT a.alias, a.normalized_alias, a.confidence,
                  c.concept_id, c.concept_type, c.canonical_name
           FROM semantic_aliases a
           JOIN semantic_concepts c
             ON c.concept_id=a.concept_id AND c.version=a.concept_version
           JOIN (
               SELECT concept_id, MAX(version) AS version
               FROM semantic_concepts
               WHERE status='ACTIVE'
               GROUP BY concept_id
           ) latest
             ON latest.concept_id=c.concept_id AND latest.version=c.version
           WHERE a.status='ACTIVE' AND c.status='ACTIVE'
           ORDER BY LENGTH(a.normalized_alias) DESC"""
    ).fetchall()

    best_by_concept: dict[str, ResolvedConcept] = {}
    for row in rows:
        alias = row["normalized_alias"]
        match = re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered)
        if not match:
            continue
        candidate = ResolvedConcept(
            concept_id=row["concept_id"],
            concept_type=row["concept_type"],
            canonical_name=row["canonical_name"],
            matched_alias=text[match.start():match.end()],
            start=match.start(),
            end=match.end(),
            confidence=float(row["confidence"]),
        )
        current = best_by_concept.get(candidate.concept_id)
        if current is None or (candidate.end - candidate.start) > (current.end - current.start):
            best_by_concept[candidate.concept_id] = candidate
    return tuple(sorted(best_by_concept.values(), key=lambda item: (item.start, item.concept_id)))


def relationships_for_concepts(
    conn: sqlite3.Connection,
    concept_ids,
) -> tuple[dict, ...]:
    present = sorted(set(concept_ids))
    if not present:
        return ()
    placeholders = ",".join("?" for _ in present)
    rows = conn.execute(
        f"""SELECT source_concept_id, relationship_type, target_concept_id
            FROM semantic_relationships
            WHERE status='ACTIVE'
              AND source_concept_id IN ({placeholders})
              AND target_concept_id IN ({placeholders})""",
        (*present, *present),
    ).fetchall()
    return tuple(
        {
            "source_id": row["source_concept_id"],
            "relationship": row["relationship_type"],
            "target_id": row["target_concept_id"],
        }
        for row in rows
    )


def related_concept_ids(
    conn: sqlite3.Connection,
    concept_id: str,
    include_self: bool = True,
) -> frozenset[str]:
    related = {concept_id} if include_self else set()
    rows = conn.execute(
        """SELECT source_concept_id, target_concept_id
           FROM semantic_relationships
           WHERE status='ACTIVE'
             AND (source_concept_id=? OR target_concept_id=?)""",
        (concept_id, concept_id),
    ).fetchall()
    for row in rows:
        if row["source_concept_id"] == concept_id:
            related.add(row["target_concept_id"])
        if row["target_concept_id"] == concept_id:
            related.add(row["source_concept_id"])
    return frozenset(related)


def semantic_match(conn: sqlite3.Connection, query_text: str, evidence_text: str) -> dict:
    query_ids = {item.concept_id for item in resolve_mentions(conn, query_text)}
    evidence_ids = {item.concept_id for item in resolve_mentions(conn, evidence_text)}
    if not query_ids:
        return {
            "score": 0.0,
            "query_concepts": [],
            "evidence_concepts": sorted(evidence_ids),
            "direct_matches": [],
            "related_matches": [],
        }

    direct = query_ids & evidence_ids
    expanded: set[str] = set()
    for concept_id in query_ids:
        expanded.update(related_concept_ids(conn, concept_id, include_self=False))
    related = (expanded & evidence_ids) - direct
    weighted = len(direct) + (0.7 * len(related))
    score = min(1.0, weighted / len(query_ids))
    return {
        "score": round(score, 4),
        "query_concepts": sorted(query_ids),
        "evidence_concepts": sorted(evidence_ids),
        "direct_matches": sorted(direct),
        "related_matches": sorted(related),
    }


def concept_labels(conn: sqlite3.Connection, concept_ids: list[str]) -> list[str]:
    labels = []
    for concept_id in concept_ids:
        concept = get_concept(conn, concept_id)
        labels.append(concept["canonical_name"] if concept else concept_id)
    return sorted(labels)


SEMANTIC_CHANGE_TYPES = {"ADD_ALIAS", "ADD_EXTERNAL_MAPPING", "UPDATE_CONCEPT"}


def validate_semantic_change(
    conn: sqlite3.Connection,
    change_type: str,
    concept_id: str,
    payload: dict,
) -> None:
    if change_type not in SEMANTIC_CHANGE_TYPES:
        raise ValueError("Unsupported semantic change type")
    if not get_concept(conn, concept_id):
        raise LookupError("Semantic concept not found")

    if change_type == "ADD_ALIAS":
        alias = str(payload.get("alias", "")).strip()
        if len(alias) < 2:
            raise ValueError("ADD_ALIAS requires an alias of at least 2 characters")
        confidence = float(payload.get("confidence", 1.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("Alias confidence must be between 0 and 1")
    elif change_type == "ADD_EXTERNAL_MAPPING":
        if not str(payload.get("system", "")).strip():
            raise ValueError("External mapping system is required")
        if not str(payload.get("identifier", "")).strip():
            raise ValueError("External mapping identifier is required")
        if not str(payload.get("source_uri", "")).strip():
            raise ValueError("External mapping source_uri is required")
    elif change_type == "UPDATE_CONCEPT":
        canonical_name = str(payload.get("canonical_name", "")).strip()
        if len(canonical_name) < 2:
            raise ValueError("UPDATE_CONCEPT requires canonical_name")


def _clone_concept_version(
    conn: sqlite3.Connection,
    concept_id: str,
    created_at_utc: str,
    canonical_name: str | None = None,
) -> int:
    current = get_concept(conn, concept_id)
    if not current:
        raise LookupError("Semantic concept not found")

    old_version = int(current["version"])
    new_version = old_version + 1
    new_name = canonical_name.strip() if canonical_name else current["canonical_name"]

    conn.execute(
        "UPDATE semantic_concepts SET status='SUPERSEDED' WHERE concept_id=? AND version=?",
        (concept_id, old_version),
    )
    conn.execute(
        """INSERT INTO semantic_concepts
           (concept_id, version, concept_type, canonical_name, semantic_version,
            status, created_at_utc)
           VALUES (?, ?, ?, ?, 'pharma-v3', 'ACTIVE', ?)""",
        (concept_id, new_version, current["concept_type"], new_name, created_at_utc),
    )

    aliases = conn.execute(
        """SELECT alias, normalized_alias, source, confidence
           FROM semantic_aliases
           WHERE concept_id=? AND concept_version=? AND status='ACTIVE'""",
        (concept_id, old_version),
    ).fetchall()
    conn.execute(
        """UPDATE semantic_aliases SET status='SUPERSEDED'
           WHERE concept_id=? AND concept_version=? AND status='ACTIVE'""",
        (concept_id, old_version),
    )
    for row in aliases:
        conn.execute(
            """INSERT INTO semantic_aliases
               (alias_id, concept_id, concept_version, alias, normalized_alias,
                source, confidence, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)""",
            (
                f"ALS_{uuid.uuid4().hex[:12].upper()}",
                concept_id,
                new_version,
                row["alias"],
                row["normalized_alias"],
                row["source"],
                row["confidence"],
                created_at_utc,
            ),
        )

    mappings = conn.execute(
        """SELECT system, identifier, source_uri
           FROM semantic_external_mappings
           WHERE concept_id=? AND concept_version=? AND status='ACTIVE'""",
        (concept_id, old_version),
    ).fetchall()
    conn.execute(
        """UPDATE semantic_external_mappings SET status='SUPERSEDED'
           WHERE concept_id=? AND concept_version=? AND status='ACTIVE'""",
        (concept_id, old_version),
    )
    for row in mappings:
        conn.execute(
            """INSERT INTO semantic_external_mappings
               (mapping_id, concept_id, concept_version, system, identifier,
                source_uri, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)""",
            (
                f"MAP_{uuid.uuid4().hex[:12].upper()}",
                concept_id,
                new_version,
                row["system"],
                row["identifier"],
                row["source_uri"],
                created_at_utc,
            ),
        )

    relationships = conn.execute(
        """SELECT relationship_id, source_concept_id, source_version,
                  relationship_type, target_concept_id, target_version
           FROM semantic_relationships
           WHERE status='ACTIVE'
             AND (source_concept_id=? OR target_concept_id=?)""",
        (concept_id, concept_id),
    ).fetchall()
    for row in relationships:
        source_id = row["source_concept_id"]
        target_id = row["target_concept_id"]
        source_version = new_version if source_id == concept_id else int(get_concept(conn, source_id)["version"])
        target_version = new_version if target_id == concept_id else int(get_concept(conn, target_id)["version"])
        conn.execute(
            "UPDATE semantic_relationships SET status='SUPERSEDED' WHERE relationship_id=?",
            (row["relationship_id"],),
        )
        conn.execute(
            """INSERT OR IGNORE INTO semantic_relationships
               (relationship_id, source_concept_id, source_version, relationship_type,
                target_concept_id, target_version, semantic_version, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'pharma-v3', 'ACTIVE', ?)""",
            (
                f"SEMREL_{uuid.uuid4().hex[:12].upper()}",
                source_id,
                source_version,
                row["relationship_type"],
                target_id,
                target_version,
                created_at_utc,
            ),
        )

    return new_version


def apply_semantic_change(
    conn: sqlite3.Connection,
    change_type: str,
    concept_id: str,
    payload: dict,
    created_at_utc: str,
) -> int:
    validate_semantic_change(conn, change_type, concept_id, payload)

    canonical_name = payload.get("canonical_name") if change_type == "UPDATE_CONCEPT" else None
    new_version = _clone_concept_version(
        conn,
        concept_id,
        created_at_utc,
        canonical_name=canonical_name,
    )

    if change_type == "ADD_ALIAS":
        alias = str(payload["alias"]).strip()
        normalized = normalize_alias(alias)
        exists = conn.execute(
            """SELECT alias_id FROM semantic_aliases
               WHERE concept_id=? AND concept_version=? AND normalized_alias=? AND status='ACTIVE'""",
            (concept_id, new_version, normalized),
        ).fetchone()
        if exists:
            raise ValueError("Alias already exists for this concept")
        conn.execute(
            """INSERT INTO semantic_aliases
               (alias_id, concept_id, concept_version, alias, normalized_alias,
                source, confidence, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?)""",
            (
                f"ALS_{uuid.uuid4().hex[:12].upper()}",
                concept_id,
                new_version,
                alias,
                normalized,
                str(payload.get("source", "GOVERNED_CHANGE")).strip() or "GOVERNED_CHANGE",
                float(payload.get("confidence", 1.0)),
                created_at_utc,
            ),
        )
    elif change_type == "ADD_EXTERNAL_MAPPING":
        conn.execute(
            """INSERT INTO semantic_external_mappings
               (mapping_id, concept_id, concept_version, system, identifier,
                source_uri, status, created_at_utc)
               VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?)""",
            (
                f"MAP_{uuid.uuid4().hex[:12].upper()}",
                concept_id,
                new_version,
                str(payload["system"]).strip().upper(),
                str(payload["identifier"]).strip(),
                str(payload["source_uri"]).strip(),
                created_at_utc,
            ),
        )

    return new_version
