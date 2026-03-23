"""
Neo4j Query Layer — retrieves IFC class data from the knowledge graph.

Provides:
    get_class_requirements(class_code) -> dict
    list_classes(search_term) -> list[dict]

Usage:
    from neuro_agent import get_class_requirements
    rules = get_class_requirements("IfcActuator")
"""

import json
import logging

import neo4j

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD

logger = logging.getLogger(__name__)

_driver: neo4j.Driver | None = None


def _get_driver() -> neo4j.Driver:
    """Return a cached Neo4j driver instance."""
    global _driver
    if _driver is None:
        _driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


def get_class_requirements(class_code: str) -> dict:
    """
    Query Neo4j for all PropertySets and Properties required by a given IFC class.

    Returns a dict like:
        {
            "class_code": "IfcActuator",
            "class_name": "Actuator",
            "class_definition": "An actuator is ...",
            "parent_class": "IfcDistributionControlElement",
            "property_sets": {
                "Pset_ActuatorTypeCommon": {
                    "pset_name": "Property Set: Actuator Type Common",
                    "pset_definition": "...",
                    "properties": [
                        {
                            "code": "ActuatorApplication",
                            "name": "Actuator Application",
                            "definition": "...",
                            "data_type": "String",
                            "property_value_kind": "Single",
                            "allowed_values": [{"Code": "DAMPERACTUATOR", ...}]
                        }
                    ]
                }
            }
        }

    Returns a dict with empty property_sets if the class is not found.
    """
    driver = _get_driver()

    query = """
    MATCH (c:Class {code: $class_code})
    OPTIONAL MATCH (c)-[:HAS_PROPERTY_SET]->(pset:PropertySet)-[:HAS_PROPERTY]->(prop:Property)
    OPTIONAL MATCH (c)-[:INHERITS_FROM]->(parent:Class)
    RETURN c.code              AS class_code,
           c.name              AS class_name,
           c.definition        AS class_definition,
           parent.code         AS parent_class_code,
           pset.code           AS pset_code,
           pset.name           AS pset_name,
           pset.definition     AS pset_definition,
           prop.code           AS prop_code,
           prop.name           AS prop_name,
           prop.definition     AS prop_definition,
           prop.data_type      AS prop_data_type,
           prop.property_value_kind AS prop_value_kind,
           prop.allowed_values AS prop_allowed_values
    """

    with driver.session() as session:
        records = list(session.run(query, class_code=class_code))

    if not records:
        logger.warning("Class '%s' not found in knowledge graph.", class_code)
        return {
            "class_code": class_code,
            "class_name": None,
            "class_definition": None,
            "parent_class": None,
            "property_sets": {},
        }

    # All records share the same class-level info
    first = records[0]
    result = {
        "class_code": first["class_code"],
        "class_name": first["class_name"],
        "class_definition": first["class_definition"],
        "parent_class": first["parent_class_code"],
        "property_sets": {},
    }

    for rec in records:
        pset_code = rec["pset_code"]
        if pset_code is None:
            continue

        if pset_code not in result["property_sets"]:
            result["property_sets"][pset_code] = {
                "pset_name": rec["pset_name"],
                "pset_definition": rec["pset_definition"],
                "properties": [],
            }

        prop_code = rec["prop_code"]
        if prop_code is None:
            continue

        # Avoid duplicate properties within the same pset
        existing_codes = {
            p["code"] for p in result["property_sets"][pset_code]["properties"]
        }
        if prop_code in existing_codes:
            continue

        # Deserialize allowed_values from JSON string
        allowed_raw = rec["prop_allowed_values"]
        allowed = json.loads(allowed_raw) if allowed_raw else []

        result["property_sets"][pset_code]["properties"].append({
            "code": prop_code,
            "name": rec["prop_name"],
            "definition": rec["prop_definition"],
            "data_type": rec["prop_data_type"],
            "property_value_kind": rec["prop_value_kind"],
            "allowed_values": allowed,
        })

    logger.info(
        "Retrieved requirements for %s: %d property sets.",
        class_code,
        len(result["property_sets"]),
    )
    return result


def get_class_structure(class_code: str) -> dict:
    """
    Query Neo4j for the EXPRESS structural definition of an IFC class:
    explicit attributes with types/optionality, inverse attributes,
    full inheritance chain, and WHERE rules.

    Returns a dict like:
        {
            "class_code": "IfcWall",
            "abstract": false,
            "where_rules": [...],
            "inheritance_chain": ["IfcBuiltElement", "IfcElement", ...],
            "attributes": [
                {
                    "name": "PredefinedType",
                    "optional": true,
                    "aggregate_kind": null,
                    "raw_type": "IfcWallTypeEnum",
                    "type_target": "IfcWallTypeEnum",
                    "type_label": "Enumeration",
                    "is_inverse": false,
                    "position": 0,
                    "refers_to_class": null,
                    "declaring_entity": "IfcWall"
                }
            ]
        }
    """
    driver = _get_driver()

    # Fetch class info + attributes + type targets + class references
    query = """
    MATCH (c:Class {code: $class_code})
    OPTIONAL MATCH (c)-[:HAS_ATTRIBUTE]->(a:Attribute)
    OPTIONAL MATCH (a)-[:ATTRIBUTE_TYPE]->(t)
    OPTIONAL MATCH (a)-[:REFERS_TO_CLASS]->(ref:Class)
    RETURN c.code              AS class_code,
           c.abstract          AS abstract,
           c.where_rules       AS where_rules,
           a.name              AS attr_name,
           a.optional          AS attr_optional,
           a.aggregate_kind    AS attr_aggregate,
           a.bounds            AS attr_bounds,
           a.raw_type          AS attr_raw_type,
           a.position          AS attr_position,
           a.is_inverse        AS attr_is_inverse,
           a.for_attribute     AS attr_for_attribute,
           a.declaring_entity  AS attr_declaring_entity,
           labels(t)[0]        AS type_label,
           CASE
               WHEN t:Class THEN t.code
               ELSE t.name
           END                 AS type_target,
           ref.code            AS refers_to_class
    ORDER BY a.is_inverse, a.position
    """

    with driver.session() as session:
        records = list(session.run(query, class_code=class_code))

    if not records:
        logger.warning("Class '%s' not found in knowledge graph.", class_code)
        return {"class_code": class_code, "abstract": None, "inheritance_chain": [], "attributes": [], "where_rules": []}

    first = records[0]

    # Parse where_rules from JSON string
    where_raw = first["where_rules"]
    where_rules = json.loads(where_raw) if where_raw else []

    result = {
        "class_code": first["class_code"],
        "abstract": first["abstract"] or False,
        "where_rules": where_rules,
        "attributes": [],
        "inheritance_chain": [],
    }

    # Deduplicate attributes
    seen_attrs = set()
    for rec in records:
        attr_name = rec["attr_name"]
        if attr_name is None or attr_name in seen_attrs:
            continue
        seen_attrs.add(attr_name)
        result["attributes"].append({
            "name": attr_name,
            "optional": rec["attr_optional"],
            "aggregate_kind": rec["attr_aggregate"],
            "bounds": rec["attr_bounds"],
            "raw_type": rec["attr_raw_type"],
            "type_target": rec["type_target"],
            "type_label": rec["type_label"],
            "is_inverse": rec["attr_is_inverse"],
            "position": rec["attr_position"],
            "for_attribute": rec["attr_for_attribute"],
            "refers_to_class": rec["refers_to_class"],
            "declaring_entity": rec["attr_declaring_entity"],
        })

    # Fetch full inheritance chain via variable-length path
    chain_query = """
    MATCH (c:Class {code: $class_code})-[:INHERITS_FROM*]->(ancestor:Class)
    RETURN ancestor.code AS code
    """
    with driver.session() as session:
        chain_records = list(session.run(chain_query, class_code=class_code))
    result["inheritance_chain"] = [r["code"] for r in chain_records]

    logger.info(
        "Retrieved structure for %s: %d attributes, %d ancestors.",
        class_code,
        len(result["attributes"]),
        len(result["inheritance_chain"]),
    )
    return result


def list_classes(search_term: str | None = None) -> list[dict]:
    """
    List IFC classes, optionally filtered by a case-insensitive name search.

    Returns a list of dicts: [{"code": "IfcWall", "name": "Wall", "definition": "..."}]
    """
    driver = _get_driver()

    if search_term:
        query = """
        MATCH (c:Class)
        WHERE toLower(c.name) CONTAINS toLower($term)
           OR toLower(c.code) CONTAINS toLower($term)
        RETURN c.code AS code, c.name AS name, c.definition AS definition
        ORDER BY c.code
        """
        params = {"term": search_term}
    else:
        query = """
        MATCH (c:Class)
        RETURN c.code AS code, c.name AS name, c.definition AS definition
        ORDER BY c.code
        """
        params = {}

    with driver.session() as session:
        records = list(session.run(query, **params))

    return [{"code": r["code"], "name": r["name"], "definition": r["definition"]} for r in records]


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    code = sys.argv[1] if len(sys.argv) > 1 else "IfcActuator"
    result = get_class_requirements(code)
    print(json.dumps(result, indent=2, ensure_ascii=False))
