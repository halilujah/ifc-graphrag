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
