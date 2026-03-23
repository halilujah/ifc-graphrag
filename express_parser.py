"""
EXPRESS schema parser for IFC4X3.

Parses ENTITY, TYPE (simple, aggregate, enumeration, select) definitions
from an EXPRESS (.exp) file and returns structured dicts suitable for
Neo4j ingestion.

Usage:
    from express_parser import parse_express
    data = parse_express("data/IFC4X3_ADD2.exp.txt")
    # data["entities"]  -> list of entity dicts
    # data["types"]     -> list of type dicts
"""

import logging
import re

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Match: TYPE IfcFoo = <rest until END_TYPE;>
_RE_TYPE_BLOCK = re.compile(
    r"^TYPE\s+(\w+)\s*=\s*(.*?)END_TYPE;",
    re.MULTILINE | re.DOTALL,
)

# Match: ENTITY IfcFoo <rest until END_ENTITY;>
_RE_ENTITY_BLOCK = re.compile(
    r"^ENTITY\s+(\w+)(.*?)END_ENTITY;",
    re.MULTILINE | re.DOTALL,
)

# Aggregate pattern: SET/LIST/ARRAY/BAG [lo:hi] OF <type>
_RE_AGGREGATE = re.compile(
    r"(SET|LIST|ARRAY|BAG)\s*\[([^\]]*)\]\s*OF\s+(.+)",
    re.IGNORECASE,
)

# Attribute line:  AttrName : OPTIONAL? <type_expr> ;
_RE_ATTRIBUTE = re.compile(
    r"^\t(\w+)\s*:\s*(OPTIONAL\s+)?(.*?)\s*;",
    re.MULTILINE,
)

# INVERSE attribute:  AttrName : aggregate [bounds] OF EntityRef FOR AttrName;
_RE_INVERSE_ATTR = re.compile(
    r"^\t(\w+)\s*:\s*(SET|LIST|BAG|ARRAY)?\s*(?:\[([^\]]*)\])?\s*(?:OF\s+)?(\w+)\s+FOR\s+(\w+)\s*;",
    re.MULTILINE,
)

# DERIVE attribute:  AttrName : <type_expr> := <expression>;
_RE_DERIVE_ATTR = re.compile(
    r"^\t\s*(\w+)\s*:\s*(.*?)\s*:=\s*(.*?)\s*;",
    re.MULTILINE | re.DOTALL,
)

# WHERE rule:  RuleName : <expression>;
_RE_WHERE_RULE = re.compile(
    r"^\t(\w+)\s*:\s*(.*?)\s*;",
    re.MULTILINE | re.DOTALL,
)

# SUBTYPE OF (ParentEntity)
_RE_SUBTYPE = re.compile(r"SUBTYPE\s+OF\s*\((\w+)\)", re.IGNORECASE)

# ABSTRACT SUPERTYPE
_RE_ABSTRACT = re.compile(r"\bABSTRACT\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Type parsing
# ---------------------------------------------------------------------------

def _parse_type_block(name: str, body: str) -> dict:
    """Parse a single TYPE block body into a structured dict."""
    body_stripped = body.strip()

    # --- ENUMERATION ---
    if body_stripped.upper().startswith("ENUMERATION OF"):
        # Extract values between parentheses
        m = re.search(r"\((.*?)\)", body_stripped, re.DOTALL)
        values = []
        if m:
            values = [v.strip().strip(",") for v in m.group(1).split("\n")]
            values = [v.strip(",").strip() for v in values if v.strip().strip(",")]
        # Capture WHERE rules
        where_rules = _extract_where_from_type(body)
        return {
            "name": name,
            "kind": "enumeration",
            "values": values,
            "where_rules": where_rules,
        }

    # --- SELECT ---
    if body_stripped.upper().startswith("SELECT"):
        m = re.search(r"\((.*?)\)", body_stripped, re.DOTALL)
        options = []
        if m:
            options = [v.strip().strip(",") for v in m.group(1).split("\n")]
            options = [v.strip(",").strip() for v in options if v.strip().strip(",")]
        return {
            "name": name,
            "kind": "select",
            "options": options,
        }

    # --- AGGREGATE (LIST/SET/ARRAY/BAG) ---
    agg_match = _RE_AGGREGATE.match(body_stripped)
    if agg_match:
        where_rules = _extract_where_from_type(body)
        return {
            "name": name,
            "kind": "aggregate",
            "aggregate_kind": agg_match.group(1).upper(),
            "bounds": agg_match.group(2).strip(),
            "element_type": agg_match.group(3).strip().rstrip(";").strip(),
            "where_rules": where_rules,
        }

    # --- SIMPLE TYPE ---
    # e.g. "REAL", "STRING(255)", "INTEGER", "BOOLEAN", "IfcLabel", "STRING(22) FIXED"
    # Strip trailing WHERE block if present
    simple_body = body_stripped
    where_idx = simple_body.find("\n WHERE")
    if where_idx == -1:
        where_idx = simple_body.find("\nWHERE")
    where_rules = []
    if where_idx >= 0:
        where_rules = _extract_where_from_type(body)
        simple_body = simple_body[:where_idx]

    underlying = simple_body.strip().rstrip(";").strip()
    return {
        "name": name,
        "kind": "simple",
        "underlying_type": underlying,
        "where_rules": where_rules,
    }


def _extract_where_from_type(body: str) -> list:
    """Extract WHERE rules from a TYPE body."""
    # Find WHERE section
    where_match = re.search(r"\bWHERE\b\s*\n(.*)", body, re.DOTALL | re.IGNORECASE)
    if not where_match:
        return []
    where_text = where_match.group(1)
    rules = []
    for m in _RE_WHERE_RULE.finditer(where_text):
        rules.append({"name": m.group(1).strip(), "text": m.group(2).strip()})
    return rules


# ---------------------------------------------------------------------------
# Entity parsing
# ---------------------------------------------------------------------------

def _parse_entity_block(name: str, body: str) -> dict:
    """Parse a single ENTITY block body into a structured dict."""
    # ABSTRACT appears in the header lines (before first tab-indented attribute)
    header = body.split("\t")[0] if body else ""
    result = {
        "name": name,
        "abstract": bool(_RE_ABSTRACT.search(header)),
        "parent": None,
        "attributes": [],
        "inverse": [],
        "derive": [],
        "where_rules": [],
    }

    # Extract parent from SUBTYPE OF
    sub_match = _RE_SUBTYPE.search(body)
    if sub_match:
        result["parent"] = sub_match.group(1)

    # Split body into sections: attributes, INVERSE, DERIVE, WHERE
    # Sections start with a keyword at column 1 (space + keyword)
    sections = _split_entity_sections(body)

    # --- Explicit attributes ---
    attr_text = sections.get("attributes", "")
    position = 0
    for m in _RE_ATTRIBUTE.finditer(attr_text):
        attr_name = m.group(1)
        optional = bool(m.group(2))
        raw_type = m.group(3).strip()

        aggregate_kind = None
        bounds = None
        type_ref = raw_type

        agg_m = _RE_AGGREGATE.match(raw_type)
        if agg_m:
            aggregate_kind = agg_m.group(1).upper()
            bounds = agg_m.group(2).strip()
            type_ref = agg_m.group(3).strip()

        # Clean type_ref: remove UNIQUE prefix if present
        type_ref = re.sub(r"^\s*UNIQUE\s+", "", type_ref).strip()

        result["attributes"].append({
            "name": attr_name,
            "optional": optional,
            "aggregate_kind": aggregate_kind,
            "bounds": bounds,
            "type_ref": type_ref,
            "raw_type": raw_type,
            "position": position,
        })
        position += 1

    # --- INVERSE attributes ---
    inv_text = sections.get("inverse", "")
    for m in _RE_INVERSE_ATTR.finditer(inv_text):
        result["inverse"].append({
            "name": m.group(1),
            "aggregate_kind": m.group(2).upper() if m.group(2) else None,
            "bounds": m.group(3).strip() if m.group(3) else None,
            "entity_ref": m.group(4),
            "for_attr": m.group(5),
        })

    # --- DERIVE attributes ---
    derive_text = sections.get("derive", "")
    for m in _RE_DERIVE_ATTR.finditer(derive_text):
        result["derive"].append({
            "name": m.group(1),
            "type_expr": m.group(2).strip(),
            "expression": m.group(3).strip(),
        })

    # --- WHERE rules ---
    where_text = sections.get("where", "")
    for m in _RE_WHERE_RULE.finditer(where_text):
        result["where_rules"].append({
            "name": m.group(1).strip(),
            "text": m.group(2).strip(),
        })

    return result


def _split_entity_sections(body: str) -> dict:
    """
    Split an ENTITY body into sections: attributes, inverse, derive, where.

    The 'attributes' section is everything before the first keyword section.
    Keywords are INVERSE, DERIVE, WHERE, UNIQUE at start of line (with leading space).
    """
    sections = {}
    # Find section boundaries
    section_pattern = re.compile(
        r"^\s(INVERSE|DERIVE|WHERE|UNIQUE)\b",
        re.MULTILINE | re.IGNORECASE,
    )

    markers = list(section_pattern.finditer(body))

    # Attributes: from after SUBTYPE/SUPERTYPE header to first section keyword
    # Find where the header ends (after SUBTYPE OF (...); line or entity name line)
    # We look for the first line with a tab-indented attribute
    header_end = 0
    for m in re.finditer(r"\n", body):
        pos = m.start() + 1
        # Check if next content is a section keyword or a tab-indented attribute
        rest = body[pos:]
        if rest.startswith("\t") or rest.startswith(" INVERSE") or rest.startswith(" DERIVE") or rest.startswith(" WHERE") or rest.startswith(" UNIQUE"):
            header_end = pos
            break

    if markers:
        attr_end = markers[0].start()
    else:
        attr_end = len(body)

    sections["attributes"] = body[header_end:attr_end]

    # Named sections
    for i, marker in enumerate(markers):
        section_name = marker.group(1).lower()
        start = marker.end()
        end = markers[i + 1].start() if i + 1 < len(markers) else len(body)
        sections[section_name] = body[start:end]

    return sections


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_express(path: str) -> dict:
    """
    Parse an EXPRESS schema file.

    Returns:
        {
            "entities": [entity_dict, ...],
            "types": [type_dict, ...],
        }
    """
    logger.info("Parsing EXPRESS schema: %s", path)

    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # Parse all TYPE blocks
    types = []
    for m in _RE_TYPE_BLOCK.finditer(text):
        name = m.group(1)
        body = m.group(2)
        parsed = _parse_type_block(name, body)
        types.append(parsed)

    # Parse all ENTITY blocks
    entities = []
    for m in _RE_ENTITY_BLOCK.finditer(text):
        name = m.group(1)
        body = m.group(2)
        parsed = _parse_entity_block(name, body)
        entities.append(parsed)

    # Count by kind
    kind_counts = {}
    for t in types:
        kind_counts[t["kind"]] = kind_counts.get(t["kind"], 0) + 1

    logger.info(
        "Parsed %d entities, %d types (%s)",
        len(entities),
        len(types),
        ", ".join(f"{k}: {v}" for k, v in sorted(kind_counts.items())),
    )

    return {"entities": entities, "types": types}


# ---------------------------------------------------------------------------
# CLI: standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    path = sys.argv[1] if len(sys.argv) > 1 else "data/IFC4X3_ADD2.exp.txt"
    data = parse_express(path)

    print(f"\n=== Entities: {len(data['entities'])} ===")
    # Spot check IfcWall
    for e in data["entities"]:
        if e["name"] == "IfcWall":
            print(f"\nIfcWall:")
            print(json.dumps(e, indent=2))
            break

    print(f"\n=== Types: {len(data['types'])} ===")
    # Spot check enum
    for t in data["types"]:
        if t["name"] == "IfcWallTypeEnum":
            print(f"\nIfcWallTypeEnum:")
            print(json.dumps(t, indent=2))
            break

    # Spot check select
    for t in data["types"]:
        if t["name"] == "IfcActorSelect":
            print(f"\nIfcActorSelect:")
            print(json.dumps(t, indent=2))
            break

    # Stats
    total_attrs = sum(len(e["attributes"]) for e in data["entities"])
    total_inverse = sum(len(e["inverse"]) for e in data["entities"])
    total_where = sum(len(e["where_rules"]) for e in data["entities"])
    entities_with_parent = sum(1 for e in data["entities"] if e["parent"])
    abstract_count = sum(1 for e in data["entities"] if e["abstract"])
    print(f"\nStats:")
    print(f"  Total explicit attributes: {total_attrs}")
    print(f"  Total inverse attributes:  {total_inverse}")
    print(f"  Total WHERE rules:         {total_where}")
    print(f"  Entities with parent:      {entities_with_parent}")
    print(f"  Abstract entities:         {abstract_count}")
