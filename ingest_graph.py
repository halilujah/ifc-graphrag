"""
Ingest the IFC 4.3 schema into Neo4j as a knowledge graph.

Phase 1 — JSON ingestion (ifc-4.3.json):
    (:Class)-[:HAS_PROPERTY_SET]->(:PropertySet)
    (:PropertySet)-[:HAS_PROPERTY]->(:Property)
    (:Class)-[:INHERITS_FROM]->(:Class)

Phase 2 — EXPRESS enrichment (IFC4X3_ADD2.exp.txt):
    (:Class)-[:HAS_ATTRIBUTE]->(:Attribute)
    (:Attribute)-[:ATTRIBUTE_TYPE]->(:Class|:Type|:Enumeration|:SelectType)
    (:Attribute)-[:REFERS_TO_CLASS]->(:Class)
    (:Enumeration)-[:HAS_VALUE]->(:EnumValue)
    (:SelectType)-[:HAS_OPTION]->(:Class|:Type|:Enumeration|:SelectType)

Usage:
    python ingest_graph.py
"""

import json
import logging
import time

import neo4j

from config import IFC_SCHEMA_PATH, EXPRESS_SCHEMA_PATH, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from express_parser import parse_express

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


# ===========================================================================
# Phase 2: EXPRESS schema enrichment
# ===========================================================================

def create_express_constraints(driver: neo4j.Driver) -> None:
    """Create constraints and indexes for EXPRESS-derived nodes."""
    logger.info("Creating EXPRESS constraints and indexes...")
    statements = [
        "CREATE CONSTRAINT type_name_unique IF NOT EXISTS FOR (t:Type) REQUIRE t.name IS UNIQUE",
        "CREATE CONSTRAINT enum_name_unique IF NOT EXISTS FOR (e:Enumeration) REQUIRE e.name IS UNIQUE",
        "CREATE CONSTRAINT select_name_unique IF NOT EXISTS FOR (s:SelectType) REQUIRE s.name IS UNIQUE",
        "CREATE CONSTRAINT attr_qname_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.qualified_name IS UNIQUE",
        "CREATE CONSTRAINT enumval_qname_unique IF NOT EXISTS FOR (v:EnumValue) REQUIRE v.qualified_name IS UNIQUE",
        "CREATE INDEX attr_name_idx IF NOT EXISTS FOR (a:Attribute) ON (a.name)",
        "CREATE INDEX enumval_value_idx IF NOT EXISTS FOR (v:EnumValue) ON (v.value)",
    ]
    with driver.session() as session:
        for stmt in statements:
            session.run(stmt)
    logger.info("EXPRESS constraints created.")


# ---------------------------------------------------------------------------
# EXPRESS node creation
# ---------------------------------------------------------------------------

def create_type_nodes(driver: neo4j.Driver, types: list) -> None:
    """Create :Type nodes for simple and aggregate EXPRESS types."""
    rows = [
        {
            "name": t["name"],
            "kind": t["kind"],
            "underlying_type": t.get("underlying_type", ""),
            "aggregate_kind": t.get("aggregate_kind"),
            "bounds": t.get("bounds"),
            "element_type": t.get("element_type"),
            "where_rules": json.dumps(t.get("where_rules", [])) or None,
        }
        for t in types
        if t["kind"] in ("simple", "aggregate")
    ]
    if not rows:
        return
    logger.info("Creating %d Type nodes...", len(rows))
    query = """
    UNWIND $rows AS r
    MERGE (t:Type {name: r.name})
    SET t.kind            = r.kind,
        t.underlying_type = r.underlying_type,
        t.aggregate_kind  = r.aggregate_kind,
        t.bounds          = r.bounds,
        t.element_type    = r.element_type,
        t.where_rules     = r.where_rules
    """
    count = _run_in_batches(driver, query, "rows", rows)
    logger.info("Created %d Type nodes.", count)


def create_enumeration_nodes(driver: neo4j.Driver, types: list) -> None:
    """Create :Enumeration and :EnumValue nodes with HAS_VALUE relationships."""
    enums = [t for t in types if t["kind"] == "enumeration"]
    if not enums:
        return
    logger.info("Creating %d Enumeration nodes...", len(enums))

    # Enumeration nodes
    enum_rows = [{"name": e["name"]} for e in enums]
    query = """
    UNWIND $rows AS r
    MERGE (e:Enumeration {name: r.name})
    """
    _run_in_batches(driver, query, "rows", enum_rows)

    # EnumValue nodes + HAS_VALUE rels
    val_rows = []
    for e in enums:
        for v in e["values"]:
            val_rows.append({
                "qualified_name": f"{e['name']}.{v}",
                "value": v,
                "enumeration_name": e["name"],
            })

    logger.info("Creating %d EnumValue nodes...", len(val_rows))
    query = """
    UNWIND $rows AS r
    MERGE (v:EnumValue {qualified_name: r.qualified_name})
    SET v.value            = r.value,
        v.enumeration_name = r.enumeration_name
    WITH v, r
    MATCH (e:Enumeration {name: r.enumeration_name})
    MERGE (e)-[:HAS_VALUE]->(v)
    """
    _run_in_batches(driver, query, "rows", val_rows)
    logger.info("Created Enumeration + EnumValue nodes.")


def create_select_type_nodes(driver: neo4j.Driver, types: list) -> None:
    """Create :SelectType nodes."""
    selects = [t for t in types if t["kind"] == "select"]
    if not selects:
        return
    logger.info("Creating %d SelectType nodes...", len(selects))
    rows = [
        {"name": s["name"], "options": json.dumps(s["options"])}
        for s in selects
    ]
    query = """
    UNWIND $rows AS r
    MERGE (s:SelectType {name: r.name})
    SET s.options = r.options
    """
    _run_in_batches(driver, query, "rows", rows)
    logger.info("Created SelectType nodes.")


def create_express_class_nodes(driver: neo4j.Driver, entities: list) -> None:
    """MERGE :Class nodes for EXPRESS entities (creates ones missing from JSON)."""
    logger.info("Merging %d EXPRESS entity -> Class nodes...", len(entities))
    rows = [
        {
            "code": e["name"],
            "abstract": e["abstract"],
            "where_rules": json.dumps(e["where_rules"]) if e["where_rules"] else None,
        }
        for e in entities
    ]
    query = """
    UNWIND $rows AS r
    MERGE (c:Class {code: r.code})
    SET c.abstract    = r.abstract,
        c.where_rules = r.where_rules
    """
    _run_in_batches(driver, query, "rows", rows)
    logger.info("Merged EXPRESS Class nodes.")


def create_attribute_nodes(driver: neo4j.Driver, entities: list) -> None:
    """Create :Attribute nodes for explicit and inverse attributes."""
    rows = []
    for e in entities:
        for a in e["attributes"]:
            rows.append({
                "qualified_name": f"{e['name']}.{a['name']}",
                "name": a["name"],
                "optional": a["optional"],
                "aggregate_kind": a.get("aggregate_kind"),
                "bounds": a.get("bounds"),
                "raw_type": a.get("raw_type", ""),
                "type_ref": a.get("type_ref", ""),
                "declaring_entity": e["name"],
                "position": a["position"],
                "is_inverse": False,
                "for_attribute": None,
            })
        for inv in e["inverse"]:
            rows.append({
                "qualified_name": f"{e['name']}.{inv['name']}",
                "name": inv["name"],
                "optional": True,  # inverse attrs are always optional
                "aggregate_kind": inv.get("aggregate_kind"),
                "bounds": inv.get("bounds"),
                "raw_type": inv.get("entity_ref", ""),
                "type_ref": inv.get("entity_ref", ""),
                "declaring_entity": e["name"],
                "position": -1,
                "is_inverse": True,
                "for_attribute": inv.get("for_attr"),
            })

    if not rows:
        return
    logger.info("Creating %d Attribute nodes...", len(rows))
    query = """
    UNWIND $rows AS r
    MERGE (a:Attribute {qualified_name: r.qualified_name})
    SET a.name              = r.name,
        a.optional          = r.optional,
        a.aggregate_kind    = r.aggregate_kind,
        a.bounds            = r.bounds,
        a.raw_type          = r.raw_type,
        a.type_ref          = r.type_ref,
        a.declaring_entity  = r.declaring_entity,
        a.position          = r.position,
        a.is_inverse        = r.is_inverse,
        a.for_attribute     = r.for_attribute
    """
    count = _run_in_batches(driver, query, "rows", rows)
    logger.info("Created %d Attribute nodes.", count)


# ---------------------------------------------------------------------------
# EXPRESS relationship creation
# ---------------------------------------------------------------------------

def create_class_has_attribute_rels(driver: neo4j.Driver, entities: list) -> None:
    """Create (:Class)-[:HAS_ATTRIBUTE]->(:Attribute) relationships."""
    logger.info("Creating Class -> Attribute relationships...")
    rels = []
    for e in entities:
        for a in e["attributes"]:
            rels.append({
                "class_code": e["name"],
                "attr_qname": f"{e['name']}.{a['name']}",
                "position": a["position"],
            })
        for inv in e["inverse"]:
            rels.append({
                "class_code": e["name"],
                "attr_qname": f"{e['name']}.{inv['name']}",
                "position": -1,
            })

    query = """
    UNWIND $rows AS r
    MATCH (c:Class {code: r.class_code})
    MATCH (a:Attribute {qualified_name: r.attr_qname})
    MERGE (c)-[rel:HAS_ATTRIBUTE]->(a)
    ON CREATE SET rel.position = r.position
    """
    count = _run_in_batches(driver, query, "rows", rels)
    logger.info("Created %d HAS_ATTRIBUTE relationships.", count)


def _build_type_lookup(types: list, entities: list) -> dict:
    """Build a name -> label lookup for type resolution."""
    lookup = {}
    for t in types:
        if t["kind"] in ("simple", "aggregate"):
            lookup[t["name"]] = "Type"
        elif t["kind"] == "enumeration":
            lookup[t["name"]] = "Enumeration"
        elif t["kind"] == "select":
            lookup[t["name"]] = "SelectType"
    for e in entities:
        lookup[e["name"]] = "Class"
    return lookup


def create_attribute_type_rels(driver: neo4j.Driver, entities: list, type_lookup: dict) -> None:
    """Create (:Attribute)-[:ATTRIBUTE_TYPE]->(:Class|:Type|:Enumeration|:SelectType)."""
    logger.info("Creating Attribute -> Type relationships...")

    # Group by target label for efficient queries
    by_label = {}  # label -> list of {attr_qname, target_name}
    for e in entities:
        for a in e["attributes"]:
            type_ref = a.get("type_ref", "")
            label = type_lookup.get(type_ref)
            if label:
                by_label.setdefault(label, []).append({
                    "attr_qname": f"{e['name']}.{a['name']}",
                    "target_name": type_ref,
                })
        for inv in e["inverse"]:
            entity_ref = inv.get("entity_ref", "")
            label = type_lookup.get(entity_ref)
            if label:
                by_label.setdefault(label, []).append({
                    "attr_qname": f"{e['name']}.{inv['name']}",
                    "target_name": entity_ref,
                })

    total = 0
    key_field = {"Class": "code", "Type": "name", "Enumeration": "name", "SelectType": "name"}
    for label, rels in by_label.items():
        key = key_field[label]
        query = f"""
        UNWIND $rows AS r
        MATCH (a:Attribute {{qualified_name: r.attr_qname}})
        MATCH (t:{label} {{{key}: r.target_name}})
        MERGE (a)-[:ATTRIBUTE_TYPE]->(t)
        """
        count = _run_in_batches(driver, query, "rows", rels)
        total += count

    logger.info("Created %d ATTRIBUTE_TYPE relationships.", total)


def create_attribute_refers_to_class_rels(driver: neo4j.Driver, entities: list, entity_names: set) -> None:
    """Create (:Attribute)-[:REFERS_TO_CLASS]->(:Class) for entity-typed attributes."""
    logger.info("Creating Attribute -> REFERS_TO_CLASS relationships...")
    rels = []
    for e in entities:
        for a in e["attributes"]:
            type_ref = a.get("type_ref", "")
            if type_ref in entity_names:
                rels.append({
                    "attr_qname": f"{e['name']}.{a['name']}",
                    "target_code": type_ref,
                })

    if not rels:
        return
    query = """
    UNWIND $rows AS r
    MATCH (a:Attribute {qualified_name: r.attr_qname})
    MATCH (c:Class {code: r.target_code})
    MERGE (a)-[:REFERS_TO_CLASS]->(c)
    """
    count = _run_in_batches(driver, query, "rows", rels)
    logger.info("Created %d REFERS_TO_CLASS relationships.", count)


def create_express_inheritance_rels(driver: neo4j.Driver, entities: list) -> None:
    """MERGE inheritance rels from EXPRESS (validates/augments JSON-based ones)."""
    logger.info("Merging EXPRESS inheritance relationships...")
    rels = [
        {"child_code": e["name"], "parent_code": e["parent"]}
        for e in entities
        if e["parent"]
    ]
    if not rels:
        return
    query = """
    UNWIND $rows AS r
    MATCH (child:Class {code: r.child_code})
    MATCH (parent:Class {code: r.parent_code})
    MERGE (child)-[:INHERITS_FROM]->(parent)
    """
    count = _run_in_batches(driver, query, "rows", rels)
    logger.info("Merged %d EXPRESS INHERITS_FROM relationships.", count)


def create_select_option_rels(driver: neo4j.Driver, types: list, type_lookup: dict) -> None:
    """Create (:SelectType)-[:HAS_OPTION]->(:Class|:Type|:Enumeration|:SelectType)."""
    logger.info("Creating SelectType -> HAS_OPTION relationships...")
    selects = [t for t in types if t["kind"] == "select"]

    key_field = {"Class": "code", "Type": "name", "Enumeration": "name", "SelectType": "name"}
    total = 0
    by_label = {}
    for s in selects:
        for opt in s["options"]:
            label = type_lookup.get(opt)
            if label:
                by_label.setdefault(label, []).append({
                    "select_name": s["name"],
                    "target_name": opt,
                })

    for label, rels in by_label.items():
        key = key_field[label]
        query = f"""
        UNWIND $rows AS r
        MATCH (s:SelectType {{name: r.select_name}})
        MATCH (t:{label} {{{key}: r.target_name}})
        MERGE (s)-[:HAS_OPTION]->(t)
        """
        count = _run_in_batches(driver, query, "rows", rels)
        total += count

    logger.info("Created %d HAS_OPTION relationships.", total)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_counts(driver: neo4j.Driver) -> None:
    """Log node and relationship counts for verification."""
    queries = {
        "Class nodes": "MATCH (c:Class) RETURN count(c) AS cnt",
        "PropertySet nodes": "MATCH (ps:PropertySet) RETURN count(ps) AS cnt",
        "Property nodes": "MATCH (p:Property) RETURN count(p) AS cnt",
        "Attribute nodes": "MATCH (a:Attribute) RETURN count(a) AS cnt",
        "Type nodes": "MATCH (t:Type) RETURN count(t) AS cnt",
        "Enumeration nodes": "MATCH (e:Enumeration) RETURN count(e) AS cnt",
        "EnumValue nodes": "MATCH (v:EnumValue) RETURN count(v) AS cnt",
        "SelectType nodes": "MATCH (s:SelectType) RETURN count(s) AS cnt",
        "HAS_PROPERTY_SET rels": "MATCH ()-[r:HAS_PROPERTY_SET]->() RETURN count(r) AS cnt",
        "HAS_PROPERTY rels": "MATCH ()-[r:HAS_PROPERTY]->() RETURN count(r) AS cnt",
        "INHERITS_FROM rels": "MATCH ()-[r:INHERITS_FROM]->() RETURN count(r) AS cnt",
        "HAS_ATTRIBUTE rels": "MATCH ()-[r:HAS_ATTRIBUTE]->() RETURN count(r) AS cnt",
        "ATTRIBUTE_TYPE rels": "MATCH ()-[r:ATTRIBUTE_TYPE]->() RETURN count(r) AS cnt",
        "REFERS_TO_CLASS rels": "MATCH ()-[r:REFERS_TO_CLASS]->() RETURN count(r) AS cnt",
        "HAS_VALUE rels": "MATCH ()-[r:HAS_VALUE]->() RETURN count(r) AS cnt",
        "HAS_OPTION rels": "MATCH ()-[r:HAS_OPTION]->() RETURN count(r) AS cnt",
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

    # -----------------------------------------------------------------------
    # Phase 1: JSON ingestion
    # -----------------------------------------------------------------------
    data = load_json(IFC_SCHEMA_PATH)
    classes, property_sets = split_classes(data["Classes"])
    properties = data["Properties"]

    create_property_nodes(driver, properties)
    create_property_set_nodes(driver, property_sets)
    create_class_nodes(driver, classes)

    create_pset_to_property_rels(driver, property_sets)
    create_class_to_pset_rels(driver, classes)
    create_inheritance_rels(driver, classes)

    # -----------------------------------------------------------------------
    # Phase 2: EXPRESS schema enrichment
    # -----------------------------------------------------------------------
    logger.info("--- Phase 2: EXPRESS schema enrichment ---")
    express_data = parse_express(str(EXPRESS_SCHEMA_PATH))

    create_express_constraints(driver)

    # Nodes
    create_type_nodes(driver, express_data["types"])
    create_enumeration_nodes(driver, express_data["types"])
    create_select_type_nodes(driver, express_data["types"])
    create_express_class_nodes(driver, express_data["entities"])
    create_attribute_nodes(driver, express_data["entities"])

    # Relationships
    type_lookup = _build_type_lookup(express_data["types"], express_data["entities"])
    entity_names = {e["name"] for e in express_data["entities"]}

    create_class_has_attribute_rels(driver, express_data["entities"])
    create_attribute_type_rels(driver, express_data["entities"], type_lookup)
    create_attribute_refers_to_class_rels(driver, express_data["entities"], entity_names)
    create_express_inheritance_rels(driver, express_data["entities"])
    create_select_option_rels(driver, express_data["types"], type_lookup)

    verify_counts(driver)

    elapsed = time.time() - start
    logger.info("Ingestion completed in %.1f seconds.", elapsed)

    driver.close()


if __name__ == "__main__":
    main()
