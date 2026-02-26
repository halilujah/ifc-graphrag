"""
Step 1: Ingest the IFC 4.3 JSON schema into Neo4j as a knowledge graph.

Creates nodes for Class, PropertySet, and Property with relationships:
    (:Class)-[:HAS_PROPERTY_SET]->(:PropertySet)
    (:PropertySet)-[:HAS_PROPERTY]->(:Property)
    (:Class)-[:INHERITS_FROM]->(:Class)

Usage:
    python ingest_graph.py
"""

import json
import logging
import time

import neo4j

from config import IFC_SCHEMA_PATH, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

logger = logging.getLogger(__name__)

BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def clear_database(driver: neo4j.Driver) -> None:
    """Drop all nodes and relationships."""
    logger.info("Clearing existing database...")
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    logger.info("Database cleared.")


def create_constraints(driver: neo4j.Driver) -> None:
    """Create uniqueness constraints and indexes."""
    logger.info("Creating constraints and indexes...")
    statements = [
        "CREATE CONSTRAINT class_code_unique IF NOT EXISTS FOR (c:Class) REQUIRE c.code IS UNIQUE",
        "CREATE CONSTRAINT pset_code_unique IF NOT EXISTS FOR (p:PropertySet) REQUIRE p.code IS UNIQUE",
        "CREATE CONSTRAINT property_code_unique IF NOT EXISTS FOR (p:Property) REQUIRE p.code IS UNIQUE",
        "CREATE INDEX class_name_idx IF NOT EXISTS FOR (c:Class) ON (c.name)",
    ]
    with driver.session() as session:
        for stmt in statements:
            session.run(stmt)
    logger.info("Constraints and indexes created.")


# ---------------------------------------------------------------------------
# JSON loading
# ---------------------------------------------------------------------------

def load_json(path) -> dict:
    """Load the full IFC 4.3 JSON file."""
    logger.info("Loading %s ...", path)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    logger.info("JSON loaded successfully.")
    return data


def split_classes(all_classes: list) -> tuple:
    """Separate Class entries from GroupOfProperties (= PropertySet) entries."""
    classes = [c for c in all_classes if c.get("ClassType") == "Class"]
    psets = [c for c in all_classes if c.get("ClassType") == "GroupOfProperties"]
    logger.info("Found %d Class entries and %d GroupOfProperties entries.", len(classes), len(psets))
    return classes, psets


# ---------------------------------------------------------------------------
# Node creation
# ---------------------------------------------------------------------------

def _run_in_batches(driver: neo4j.Driver, query: str, param_name: str, items: list, batch_size: int = BATCH_SIZE) -> int:
    """Execute a Cypher UNWIND query in batches. Returns total items processed."""
    total = 0
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        with driver.session() as session:
            session.run(query, **{param_name: batch})
        total += len(batch)
    return total


def create_property_nodes(driver: neo4j.Driver, properties: list) -> None:
    """Create Property nodes from the top-level Properties array."""
    logger.info("Creating %d Property nodes...", len(properties))

    rows = []
    for p in properties:
        allowed = p.get("AllowedValues")
        rows.append({
            "code": p["Code"],
            "name": p.get("Name", ""),
            "definition": p.get("Definition", ""),
            "data_type": p.get("DataType"),
            "property_value_kind": p.get("PropertyValueKind", ""),
            "description": p.get("Description"),
            "allowed_values": json.dumps(allowed) if allowed else None,
        })

    query = """
    UNWIND $rows AS r
    MERGE (prop:Property {code: r.code})
    SET prop.name              = r.name,
        prop.definition        = r.definition,
        prop.data_type         = r.data_type,
        prop.property_value_kind = r.property_value_kind,
        prop.description       = r.description,
        prop.allowed_values    = r.allowed_values
    """
    count = _run_in_batches(driver, query, "rows", rows)
    logger.info("Created %d Property nodes.", count)


def create_property_set_nodes(driver: neo4j.Driver, group_of_properties: list) -> None:
    """Create PropertySet nodes from GroupOfProperties entries + synthetic 'Attributes'."""
    logger.info("Creating PropertySet nodes...")

    rows = []
    for gop in group_of_properties:
        rows.append({
            "code": gop["Code"],
            "name": gop.get("Name", ""),
            "definition": gop.get("Definition", ""),
        })

    # Add the synthetic "Attributes" PropertySet
    rows.append({
        "code": "Attributes",
        "name": "Attributes",
        "definition": "Built-in IFC element attributes (Tag, ObjectType, ElementType, etc.).",
    })

    query = """
    UNWIND $rows AS r
    MERGE (pset:PropertySet {code: r.code})
    SET pset.name       = r.name,
        pset.definition = r.definition
    """
    count = _run_in_batches(driver, query, "rows", rows)
    logger.info("Created %d PropertySet nodes.", count)


def create_class_nodes(driver: neo4j.Driver, classes: list) -> None:
    """Create Class nodes."""
    logger.info("Creating %d Class nodes...", len(classes))

    rows = []
    for c in classes:
        rows.append({
            "code": c["Code"],
            "name": c.get("Name", ""),
            "definition": c.get("Definition", ""),
            "parent_class_code": c.get("ParentClassCode"),
            "uid": c.get("Uid"),
        })

    query = """
    UNWIND $rows AS r
    MERGE (cls:Class {code: r.code})
    SET cls.name              = r.name,
        cls.definition        = r.definition,
        cls.parent_class_code = r.parent_class_code,
        cls.uid               = r.uid
    """
    count = _run_in_batches(driver, query, "rows", rows)
    logger.info("Created %d Class nodes.", count)


# ---------------------------------------------------------------------------
# Relationship creation
# ---------------------------------------------------------------------------

def create_pset_to_property_rels(driver: neo4j.Driver, group_of_properties: list) -> None:
    """Create (:PropertySet)-[:HAS_PROPERTY]->(:Property) relationships."""
    logger.info("Creating PropertySet -> Property relationships...")

    rels = []
    for gop in group_of_properties:
        pset_code = gop["Code"]
        seen_props = set()
        for cp in gop.get("ClassProperties", []):
            prop_code = cp["PropertyCode"]
            if prop_code not in seen_props:
                seen_props.add(prop_code)
                rels.append({
                    "pset_code": pset_code,
                    "prop_code": prop_code,
                    "ref_code": cp["Code"],
                })

    query = """
    UNWIND $rows AS r
    MATCH (pset:PropertySet {code: r.pset_code})
    MATCH (prop:Property {code: r.prop_code})
    MERGE (pset)-[rel:HAS_PROPERTY]->(prop)
    ON CREATE SET rel.ref_code = r.ref_code
    """
    count = _run_in_batches(driver, query, "rows", rels)
    logger.info("Created %d PropertySet->Property relationships.", count)


def create_class_to_pset_rels(driver: neo4j.Driver, classes: list) -> None:
    """Create (:Class)-[:HAS_PROPERTY_SET]->(:PropertySet) relationships."""
    logger.info("Creating Class -> PropertySet relationships...")

    rels = []
    for c in classes:
        class_code = c["Code"]
        # Extract unique PropertySet names for this class
        pset_names = set()
        for cp in c.get("ClassProperties", []):
            pset_names.add(cp["PropertySet"])
        for pset_name in pset_names:
            rels.append({
                "class_code": class_code,
                "pset_code": pset_name,
            })

    query = """
    UNWIND $rows AS r
    MATCH (cls:Class {code: r.class_code})
    MATCH (pset:PropertySet {code: r.pset_code})
    MERGE (cls)-[:HAS_PROPERTY_SET]->(pset)
    """
    count = _run_in_batches(driver, query, "rows", rels)
    logger.info("Created %d Class->PropertySet relationships.", count)


def create_inheritance_rels(driver: neo4j.Driver, classes: list) -> None:
    """Create (:Class)-[:INHERITS_FROM]->(:Class) relationships."""
    logger.info("Creating inheritance relationships...")

    rels = []
    for c in classes:
        parent = c.get("ParentClassCode")
        if parent:
            rels.append({
                "child_code": c["Code"],
                "parent_code": parent,
            })

    query = """
    UNWIND $rows AS r
    MATCH (child:Class {code: r.child_code})
    MATCH (parent:Class {code: r.parent_code})
    MERGE (child)-[:INHERITS_FROM]->(parent)
    """
    count = _run_in_batches(driver, query, "rows", rels)
    logger.info("Created %d INHERITS_FROM relationships.", count)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_counts(driver: neo4j.Driver) -> None:
    """Log node and relationship counts for verification."""
    queries = {
        "Class nodes": "MATCH (c:Class) RETURN count(c) AS cnt",
        "PropertySet nodes": "MATCH (ps:PropertySet) RETURN count(ps) AS cnt",
        "Property nodes": "MATCH (p:Property) RETURN count(p) AS cnt",
        "HAS_PROPERTY_SET rels": "MATCH ()-[r:HAS_PROPERTY_SET]->() RETURN count(r) AS cnt",
        "HAS_PROPERTY rels": "MATCH ()-[r:HAS_PROPERTY]->() RETURN count(r) AS cnt",
        "INHERITS_FROM rels": "MATCH ()-[r:INHERITS_FROM]->() RETURN count(r) AS cnt",
    }
    logger.info("--- Verification Counts ---")
    with driver.session() as session:
        for label, q in queries.items():
            result = session.run(q).single()
            logger.info("  %s: %d", label, result["cnt"])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    logger.info("Connecting to Neo4j at %s ...", NEO4J_URI)
    driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        driver.verify_connectivity()
        logger.info("Connected to Neo4j.")
    except Exception as e:
        logger.error("Failed to connect to Neo4j: %s", e)
        logger.error("Make sure Neo4j is running. You can start it with:")
        logger.error("  docker run --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/password neo4j:5")
        return

    start = time.time()

    clear_database(driver)
    create_constraints(driver)

    data = load_json(IFC_SCHEMA_PATH)
    classes, property_sets = split_classes(data["Classes"])
    properties = data["Properties"]

    # Create nodes first
    create_property_nodes(driver, properties)
    create_property_set_nodes(driver, property_sets)
    create_class_nodes(driver, classes)

    # Then create relationships
    create_pset_to_property_rels(driver, property_sets)
    create_class_to_pset_rels(driver, classes)
    create_inheritance_rels(driver, classes)

    verify_counts(driver)

    elapsed = time.time() - start
    logger.info("Ingestion completed in %.1f seconds.", elapsed)

    driver.close()


if __name__ == "__main__":
    main()
