"""
UCKS Pipeline — validates, serializes to YAML, and ingests entity definitions
into the Neo4j knowledge graph.

Usage:
    from ucks_pipeline import define_entity_from_json
    result = define_entity_from_json({"id": "wall", "name": "Wall", ...})
"""

import json
import logging
from pathlib import Path

import neo4j
import yaml

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, PROJECT_ROOT
from ucks_models import EntityDef, UcksDocument

logger = logging.getLogger(__name__)

UCKS_OUTPUT_DIR = PROJECT_ROOT / "data" / "ucks_entities"
UCKS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_driver: neo4j.Driver | None = None


def _get_driver() -> neo4j.Driver:
    global _driver
    if _driver is None:
        _driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


# ---------------------------------------------------------------------------
# YAML serialization
# ---------------------------------------------------------------------------

def _entity_to_yaml_dict(entity: EntityDef) -> dict:
    """Convert a validated EntityDef to a clean dict for YAML output."""
    d = {
        "schema": "ucks/0.1",
        "sector": entity.sector,
        "domain": entity.domain,
        "entity": {
            "id": entity.id,
            "name": entity.name,
            "description": entity.description,
        },
    }

    if entity.parent:
        d["entity"]["parent"] = entity.parent

    if entity.property_groups:
        groups = []
        for pg in entity.property_groups:
            group = {"id": pg.id, "name": pg.name}
            if pg.description:
                group["description"] = pg.description
            props = []
            for p in pg.properties:
                prop = {
                    "id": p.id,
                    "name": p.name,
                    "data_type": p.data_type,
                }
                if p.description:
                    prop["description"] = p.description
                if p.unit:
                    prop["unit"] = p.unit
                if p.required:
                    prop["required"] = True
                if p.constraints:
                    c = {}
                    if p.constraints.min is not None:
                        c["min"] = p.constraints.min
                    if p.constraints.max is not None:
                        c["max"] = p.constraints.max
                    if p.constraints.pattern:
                        c["pattern"] = p.constraints.pattern
                    if c:
                        prop["constraints"] = c
                if p.enumeration:
                    prop["enumeration"] = {
                        "id": p.enumeration.id,
                        "values": p.enumeration.values,
                    }
                if p.example:
                    prop["example"] = p.example
                props.append(prop)
            group["properties"] = props
            groups.append(group)
        d["entity"]["property_groups"] = groups

    if entity.relationships:
        rels = []
        for r in entity.relationships:
            rel = {"type": r.type, "target": r.target, "cardinality": r.cardinality}
            if r.description:
                rel["description"] = r.description
            rels.append(rel)
        d["entity"]["relationships"] = rels

    return d


def save_entity_yaml(entity: EntityDef) -> Path:
    """Save a validated entity definition as a YAML file."""
    yaml_dict = _entity_to_yaml_dict(entity)
    sector_dir = UCKS_OUTPUT_DIR / entity.sector
    sector_dir.mkdir(parents=True, exist_ok=True)
    filepath = sector_dir / f"{entity.id}.yaml"
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(yaml_dict, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    logger.info("Saved UCKS entity YAML: %s", filepath)
    return filepath


# ---------------------------------------------------------------------------
# Neo4j ingestion
# ---------------------------------------------------------------------------

def ingest_entity_to_neo4j(entity: EntityDef) -> dict:
    """Ingest a single UCKS entity definition into Neo4j."""
    driver = _get_driver()
    stats = {"nodes_created": 0, "relationships_created": 0}

    with driver.session() as session:
        # 1. Create/merge the Entity node
        session.run(
            """
            MERGE (e:UCKSEntity {id: $id})
            SET e.name = $name,
                e.description = $description,
                e.sector = $sector,
                e.domain = $domain,
                e.schema_version = 'ucks/0.1'
            """,
            id=entity.id,
            name=entity.name,
            description=entity.description,
            sector=entity.sector,
            domain=entity.domain,
        )
        stats["nodes_created"] += 1

        # 2. Parent relationship
        if entity.parent:
            session.run(
                """
                MERGE (child:UCKSEntity {id: $child_id})
                MERGE (parent:UCKSEntity {id: $parent_id})
                MERGE (child)-[:INHERITS_FROM]->(parent)
                """,
                child_id=entity.id,
                parent_id=entity.parent,
            )
            stats["relationships_created"] += 1

        # 3. Property groups and properties
        for pg in entity.property_groups:
            session.run(
                """
                MERGE (pg:UCKSPropertyGroup {id: $pg_id})
                SET pg.name = $pg_name, pg.description = $pg_desc
                WITH pg
                MATCH (e:UCKSEntity {id: $entity_id})
                MERGE (e)-[:HAS_PROPERTY_GROUP]->(pg)
                """,
                pg_id=f"{entity.id}.{pg.id}",
                pg_name=pg.name,
                pg_desc=pg.description or "",
                entity_id=entity.id,
            )
            stats["nodes_created"] += 1
            stats["relationships_created"] += 1

            for prop in pg.properties:
                enum_values = json.dumps(prop.enumeration.values) if prop.enumeration else None
                constraints_json = None
                if prop.constraints:
                    c = {}
                    if prop.constraints.min is not None:
                        c["min"] = prop.constraints.min
                    if prop.constraints.max is not None:
                        c["max"] = prop.constraints.max
                    if prop.constraints.pattern:
                        c["pattern"] = prop.constraints.pattern
                    if c:
                        constraints_json = json.dumps(c)

                session.run(
                    """
                    MERGE (p:UCKSProperty {id: $prop_id})
                    SET p.name = $name,
                        p.description = $desc,
                        p.data_type = $data_type,
                        p.unit = $unit,
                        p.required = $required,
                        p.enum_values = $enum_values,
                        p.constraints = $constraints
                    WITH p
                    MATCH (pg:UCKSPropertyGroup {id: $pg_id})
                    MERGE (pg)-[:HAS_PROPERTY]->(p)
                    """,
                    prop_id=f"{entity.id}.{pg.id}.{prop.id}",
                    name=prop.name,
                    desc=prop.description or "",
                    data_type=prop.data_type,
                    unit=prop.unit or "",
                    required=prop.required,
                    enum_values=enum_values,
                    constraints=constraints_json,
                    pg_id=f"{entity.id}.{pg.id}",
                )
                stats["nodes_created"] += 1
                stats["relationships_created"] += 1

        # 4. Relationships to other entities
        for rel in entity.relationships:
            session.run(
                """
                MERGE (source:UCKSEntity {id: $source_id})
                MERGE (target:UCKSEntity {id: $target_id})
                MERGE (source)-[r:RELATES_TO {type: $rel_type}]->(target)
                SET r.cardinality = $cardinality, r.description = $desc
                """,
                source_id=entity.id,
                target_id=rel.target,
                rel_type=rel.type,
                cardinality=rel.cardinality,
                desc=rel.description or "",
            )
            stats["relationships_created"] += 1

    logger.info(
        "Ingested UCKS entity '%s': %d nodes, %d relationships",
        entity.id, stats["nodes_created"], stats["relationships_created"],
    )
    return stats


# ---------------------------------------------------------------------------
# Main entry point (called by orchestrator tool)
# ---------------------------------------------------------------------------

def define_entity_from_json(entity_data: dict) -> dict:
    """
    Validate an entity JSON from the LLM, save as YAML, and ingest into Neo4j.

    Returns a result dict with status, file path, and graph stats.
    """
    try:
        entity = EntityDef(**entity_data)
    except Exception as e:
        return {"error": f"Validation failed: {e}"}

    # Save YAML
    yaml_path = save_entity_yaml(entity)

    # Ingest to Neo4j
    try:
        stats = ingest_entity_to_neo4j(entity)
    except Exception as e:
        logger.error("Neo4j ingestion failed for '%s': %s", entity.id, e)
        return {
            "status": "partial",
            "yaml_saved": str(yaml_path),
            "graph_error": str(e),
        }

    return {
        "status": "success",
        "entity_id": entity.id,
        "entity_name": entity.name,
        "sector": entity.sector,
        "domain": entity.domain,
        "yaml_saved": str(yaml_path),
        "graph_stats": stats,
        "property_count": sum(len(pg.properties) for pg in entity.property_groups),
        "relationship_count": len(entity.relationships),
    }


def list_ucks_entities() -> list[dict]:
    """List all UCKS entities currently in Neo4j."""
    driver = _get_driver()
    with driver.session() as session:
        result = session.run(
            """
            MATCH (e:UCKSEntity)
            WHERE e.schema_version IS NOT NULL
            OPTIONAL MATCH (e)-[:HAS_PROPERTY_GROUP]->(pg)-[:HAS_PROPERTY]->(p)
            RETURN e.id AS id, e.name AS name, e.description AS description,
                   e.sector AS sector, e.domain AS domain,
                   count(DISTINCT pg) AS property_groups,
                   count(DISTINCT p) AS properties
            ORDER BY e.sector, e.name
            """
        )
        return [dict(r) for r in result]


def get_ucks_entity_detail(entity_id: str) -> dict:
    """Get full UCKS entity details as a structured dict for export/mapping."""
    driver = _get_driver()

    with driver.session() as session:
        # Entity info
        ent = session.run(
            "MATCH (e:UCKSEntity {id: $id}) RETURN e",
            id=entity_id,
        ).single()
        if not ent:
            return {"error": f"Entity '{entity_id}' not found"}

        entity = dict(ent["e"])

        # Parent
        parent_rec = session.run(
            "MATCH (e:UCKSEntity {id: $id})-[:INHERITS_FROM]->(p:UCKSEntity) RETURN p.id AS id, p.name AS name",
            id=entity_id,
        ).single()
        entity["parent"] = dict(parent_rec) if parent_rec else None

        # Property groups + properties
        pg_records = list(session.run(
            """
            MATCH (e:UCKSEntity {id: $id})-[:HAS_PROPERTY_GROUP]->(pg:UCKSPropertyGroup)-[:HAS_PROPERTY]->(p:UCKSProperty)
            RETURN pg.id AS pg_id, pg.name AS pg_name, pg.description AS pg_desc,
                   p.name AS prop_name, p.data_type AS prop_type, p.unit AS prop_unit,
                   p.required AS prop_required, p.enum_values AS prop_enum, p.description AS prop_desc
            ORDER BY pg.name, p.name
            """,
            id=entity_id,
        ))

        groups = {}
        for rec in pg_records:
            pgid = rec["pg_id"]
            if pgid not in groups:
                groups[pgid] = {
                    "name": rec["pg_name"],
                    "description": rec["pg_desc"],
                    "properties": [],
                }
            prop = {
                "name": rec["prop_name"],
                "data_type": rec["prop_type"],
                "unit": rec["prop_unit"] or None,
                "required": rec["prop_required"],
                "description": rec["prop_desc"],
            }
            if rec["prop_enum"]:
                try:
                    prop["enum_values"] = json.loads(rec["prop_enum"])
                except (json.JSONDecodeError, TypeError):
                    pass
            groups[pgid]["properties"].append(prop)

        entity["property_groups"] = list(groups.values())

        # Relationships
        rel_records = list(session.run(
            """
            MATCH (e:UCKSEntity {id: $id})-[r:RELATES_TO]->(t:UCKSEntity)
            RETURN r.type AS type, r.cardinality AS cardinality, t.id AS target_id, t.name AS target_name
            """,
            id=entity_id,
        ))
        entity["relationships"] = [dict(r) for r in rel_records]

    return entity


def get_ucks_entity_graph(entity_id: str) -> dict:
    """Get vis.js-compatible graph data for a UCKS entity."""
    driver = _get_driver()
    nodes = {}
    edges = []

    with driver.session() as session:
        # Entity + property groups + properties
        records = list(session.run(
            """
            MATCH (e:UCKSEntity {id: $id})
            OPTIONAL MATCH (e)-[:HAS_PROPERTY_GROUP]->(pg:UCKSPropertyGroup)-[:HAS_PROPERTY]->(p:UCKSProperty)
            OPTIONAL MATCH (e)-[:INHERITS_FROM]->(parent:UCKSEntity)
            RETURN e, pg, p, parent
            """,
            id=entity_id,
        ))

        if not records:
            return {"nodes": [], "edges": [], "error": f"Entity '{entity_id}' not found"}

        for rec in records:
            e = rec["e"]
            if e and f"entity:{e['id']}" not in nodes:
                nodes[f"entity:{e['id']}"] = {
                    "id": f"entity:{e['id']}",
                    "label": e["name"],
                    "title": e.get("description", ""),
                    "group": "entity",
                    "meta": dict(e),
                }

            parent = rec["parent"]
            if parent and f"entity:{parent['id']}" not in nodes:
                nodes[f"entity:{parent['id']}"] = {
                    "id": f"entity:{parent['id']}",
                    "label": parent["name"] if parent.get("name") else parent["id"],
                    "title": parent.get("description", ""),
                    "group": "parent",
                    "meta": dict(parent),
                }
                edges.append({"from": f"entity:{e['id']}", "to": f"entity:{parent['id']}", "label": "INHERITS_FROM", "arrows": "to"})

            pg = rec["pg"]
            if pg and f"pg:{pg['id']}" not in nodes:
                nodes[f"pg:{pg['id']}"] = {
                    "id": f"pg:{pg['id']}",
                    "label": pg["name"],
                    "title": pg.get("description", ""),
                    "group": "pset",
                    "meta": dict(pg),
                }
                edges.append({"from": f"entity:{e['id']}", "to": f"pg:{pg['id']}", "label": "HAS_PROPERTY_GROUP", "arrows": "to"})

            p = rec["p"]
            if p and f"prop:{p['id']}" not in nodes:
                tooltip = p.get("description", p["name"])
                if p.get("data_type"):
                    tooltip += f"\nType: {p['data_type']}"
                if p.get("unit"):
                    tooltip += f"\nUnit: {p['unit']}"
                nodes[f"prop:{p['id']}"] = {
                    "id": f"prop:{p['id']}",
                    "label": p["name"],
                    "title": tooltip,
                    "group": "property",
                    "meta": dict(p),
                }
                if pg:
                    edges.append({"from": f"pg:{pg['id']}", "to": f"prop:{p['id']}", "label": "HAS_PROPERTY", "arrows": "to"})

        # Relationships to other entities
        rel_records = list(session.run(
            """
            MATCH (e:UCKSEntity {id: $id})-[r:RELATES_TO]->(t:UCKSEntity)
            RETURN r.type AS rel_type, r.cardinality AS cardinality, t.id AS target_id, t.name AS target_name, t.description AS target_desc
            """,
            id=entity_id,
        ))

        for rec in rel_records:
            tid = f"entity:{rec['target_id']}"
            if tid not in nodes:
                nodes[tid] = {
                    "id": tid,
                    "label": rec["target_name"] or rec["target_id"],
                    "title": rec.get("target_desc", ""),
                    "group": "entity",
                    "meta": {"id": rec["target_id"], "name": rec["target_name"]},
                }
            edges.append({
                "from": f"entity:{entity_id}",
                "to": tid,
                "label": f"{rec['rel_type']} ({rec['cardinality']})",
                "arrows": "to",
                "dashes": True,
            })

    return {"nodes": list(nodes.values()), "edges": edges}
