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
