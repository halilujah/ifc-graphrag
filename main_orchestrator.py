"""
IFC Semantic Query Engine — Gemini-powered agent that answers questions
about IFC 4.3 using a Neo4j knowledge graph.

Tools:
  1. query_class     — Get all PropertySets/Properties for a class
  2. search_classes  — Search classes by keyword
  3. run_cypher      — Execute read-only Cypher queries on Neo4j

Usage:
    # As a library (called by web_app.py):
    from main_orchestrator import run_agent
    answer, history, tool_log = run_agent("What properties does IfcBridge need?")

    # CLI:
    python main_orchestrator.py -q "What is IfcActuator?"
"""

import json
import logging
import re
import time

import neo4j
from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from neuro_agent import get_class_requirements, get_class_structure, list_classes
from ids_pipeline import generate_ids_from_json
from ucks_pipeline import define_entity_from_json, list_ucks_entities, get_ucks_entity_detail

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 10
MAX_RETRIES = 3
RETRY_BASE_DELAY = 10  # seconds

# Write-operation keywords to block in run_cypher
_WRITE_KEYWORDS = re.compile(
    r"\b(CREATE|DELETE|DETACH|SET|REMOVE|MERGE|DROP|CALL\s*\{)\b", re.IGNORECASE
)

SYSTEM_PROMPT = """\
You are a Civil Engineering Knowledge Engine. You have two modes:

MODE 1 — QUERY: Answer questions about the IFC 4.3 standard and UCKS entities
using a Neo4j knowledge graph.

MODE 2 — DEFINE: Help domain experts define new civil engineering concepts in the
Universal Civil Knowledge Schema (UCKS) format through natural language conversation.

=== KNOWLEDGE GRAPH STRUCTURE ===

IFC Layer (read-only, from bSDD + EXPRESS):
  (:Class {code, name, definition}) -[:HAS_PROPERTY_SET]-> (:PropertySet)
  (:PropertySet) -[:HAS_PROPERTY]-> (:Property {code, name, data_type, allowed_values})
  (:Class) -[:INHERITS_FROM]-> (:Class)
  (:Class) -[:HAS_ATTRIBUTE]-> (:Attribute {name, optional, raw_type, is_inverse})
  (:Attribute) -[:ATTRIBUTE_TYPE]-> (:Class | :Type | :Enumeration | :SelectType)

UCKS Layer (read-write, user-defined):
  (:UCKSEntity {id, name, description, sector, domain})
  (:UCKSEntity) -[:INHERITS_FROM]-> (:UCKSEntity)
  (:UCKSEntity) -[:HAS_PROPERTY_GROUP]-> (:UCKSPropertyGroup {id, name})
  (:UCKSPropertyGroup) -[:HAS_PROPERTY]-> (:UCKSProperty {id, name, data_type, unit})
  (:UCKSEntity) -[:RELATES_TO {type, cardinality}]-> (:UCKSEntity)

=== TOOLS ===

IFC Query tools:
  - query_class: Get PropertySets/Properties for an IFC class
  - query_class_structure: Get EXPRESS attributes, inheritance, WHERE rules
  - search_classes: Search IFC classes by keyword
  - run_cypher: Execute read-only Cypher queries

IFC Generation tools:
  - generate_ids: Generate IDS XML specification

UCKS tools:
  - define_entity: Define a new civil engineering entity in UCKS format.
    Validates, saves as YAML, and ingests into Neo4j.
  - list_ucks_entities: List all UCKS entities currently in the knowledge library.
  - get_ucks_entity: Get full details of a UCKS entity (properties, relationships).

=== UCKS ENTITY DEFINITION GUIDELINES ===

When the user describes a civil engineering concept (building element, infrastructure
component, facility equipment, etc.), structure it into a UCKS entity:

1. Extract the concept: What is it? What sector (building/infrastructure/facility/urban)?
   What domain (structural/architectural/mechanical/etc.)?
2. Identify properties: What measurable or describable attributes does it have?
   Group related properties together. Assign correct data types (string/real/integer/boolean/enum/date).
3. Identify relationships: What other entities does it connect to? (contains, supports, serves, etc.)
4. Determine inheritance: Is it a type of something more general? (e.g., bridge_deck -> structural_element)
5. Call define_entity with the structured JSON.

Key rules for define_entity:
  - id must be snake_case (e.g. "bridge_deck", "air_handling_unit")
  - sector: one of "building", "infrastructure", "facility", "urban", "general"
  - domain: e.g. "structural", "architectural", "mechanical", "electrical", "geotechnical"
  - data_type: one of "string", "real", "integer", "boolean", "enum", "date"
  - Include units for numeric properties (e.g. "mm", "kN", "m3/h")
  - Use meaningful property group names (e.g. "structural_properties", "performance_properties")
  - Be generous with properties — capture everything the user mentions plus obvious ones
  - Add constraints where appropriate (min/max for numbers, patterns for strings)
  - Add enumerations for categorical values

Example: If user says "A retaining wall holds back soil, it has height, thickness,
and can be cantilever or gravity type", you should define:
  - Entity: retaining_wall, sector=infrastructure, domain=geotechnical
  - Properties: height (real, m), thickness (real, mm), wall_type (enum: CANTILEVER, GRAVITY, ANCHORED)
  - Parent: wall or structural_element
  - Relationships: retains -> soil_mass, founded_on -> foundation

IMPORTANT: When defining entities, do NOT ask the user for every detail. Make reasonable
engineering decisions. Use your knowledge of civil engineering to fill in obvious
properties, relationships, and constraints. The user is the domain expert for the
concept — you are the schema expert for structuring it.

You can also use IFC as reference: query_class or search_classes to see how IFC
defines a similar concept, then create a cleaner UCKS version.

=== UCKS TO IFC EXPORT ===

When the user asks to export a UCKS entity to IFC (or "convert to IFC", "map to IFC"):

1. Call get_ucks_entity to retrieve the full UCKS entity details.
2. Use search_classes to find the closest IFC class(es) for this concept.
3. Use query_class on the best match to get its PropertySets and Properties.
4. Produce a MAPPING REPORT showing:
   - UCKS entity → IFC class (with PredefinedType if applicable)
   - Each UCKS property → IFC PropertySet.Property (or "custom" if no match)
   - Each UCKS relationship → IFC relationship type
   - Data type mappings (UCKS real → IFC IfcLengthMeasure, etc.)
5. Then call generate_ids to produce an IDS specification that combines:
   - The IFC class from the mapping
   - All mapped properties as requirements
   - Custom properties as additional requirements with suggested Pset names

Data type mapping for IDS:
  - UCKS string → IFCTEXT
  - UCKS real → IFCREAL (or IFCLENGTHMEASURE/IFCAREAMEASURE based on unit)
  - UCKS integer → IFCINTEGER
  - UCKS boolean → IFCBOOLEAN
  - UCKS enum → IFCLABEL
  - UCKS date → IFCTEXT

For custom properties without an IFC match, suggest a PropertySet name like
"Pset_<EntityName>Custom" (e.g. "Pset_BridgePierCustom").

=== IFC QUERY GUIDELINES ===

  - Use query_class for property-related questions (PropertySets, Properties).
  - Use query_class_structure for structural questions (attributes, inheritance).
  - Use search_classes to find classes by keyword.
  - Use run_cypher for aggregations, comparisons, multi-node traversals.
  - Always ground answers in actual graph data returned by tools.
  - Be concise but thorough. Use bullet points and structured formatting.

=== IDS GENERATION ===

  When asked to generate an IDS specification:
  1. Use query_class to find exact PropertySet/Property names.
  2. Call generate_ids with structured JSON.
  - Entity names MUST be UPPERCASE (e.g. "IFCWALL").
  - dataType MUST be UPPERCASE IFC type (e.g. "IFCTEXT", "IFCBOOLEAN").
  - Data type mapping: String->IFCTEXT, Real->IFCREAL, Boolean->IFCBOOLEAN,
    Integer->IFCINTEGER, Character->IFCLABEL.
"""

# ---------------------------------------------------------------------------
# Tool declarations (Gemini function calling format)
# ---------------------------------------------------------------------------

TOOL_DECLARATIONS = [
    {
        "name": "query_class",
        "description": (
            "Query the IFC 4.3 knowledge graph for all required PropertySets "
            "and Properties of a given IFC class. Returns class info, parent class, "
            "and a dict of property sets with their properties."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "class_code": {
                    "type": "string",
                    "description": "The IFC class code, e.g. 'IfcWall', 'IfcBridge', 'IfcActuator'",
                }
            },
            "required": ["class_code"],
        },
    },
    {
        "name": "search_classes",
        "description": (
            "Search IFC classes by keyword. Matches against class code and name. "
            "Returns a list of matching classes with code, name, and definition."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Keyword to search for, e.g. 'bridge', 'wall', 'sensor'",
                }
            },
            "required": ["search_term"],
        },
    },
    {
        "name": "query_class_structure",
        "description": (
            "Get the EXPRESS structural definition of an IFC class: "
            "explicit attributes with types and optionality, inverse attributes, "
            "full inheritance chain, and WHERE validation rules. "
            "Use this for structural questions about placement, representation, "
            "entity dependencies, and attribute types."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "class_code": {
                    "type": "string",
                    "description": "The IFC class code, e.g. 'IfcWall', 'IfcProduct', 'IfcBuildingElement'",
                }
            },
            "required": ["class_code"],
        },
    },
    {
        "name": "run_cypher",
        "description": (
            "Execute a read-only Cypher query against the Neo4j knowledge graph. "
            "Use this for complex questions: aggregations, finding shared property sets, "
            "counting, comparing classes, traversing inheritance chains, etc. "
            "Only MATCH/RETURN/WITH/WHERE/ORDER BY/LIMIT/OPTIONAL MATCH are allowed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A read-only Cypher query, e.g. MATCH (c:Class)-[:HAS_PROPERTY_SET]->(ps) RETURN c.code, count(ps) ORDER BY count(ps) DESC LIMIT 10",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "generate_ids",
        "description": (
            "Generate an IDS (Information Delivery Specification) XML file from a "
            "structured JSON specification. Call this AFTER using query_class to find "
            "exact PropertySet names, Property names, and data types. The JSON must "
            "have 'info' (with 'title') and 'specifications' (list of specs, each "
            "with 'name', 'ifcVersion', 'applicability' with 'entity', and "
            "'requirements' with 'properties' and/or 'attributes'). "
            "Entity names must be UPPERCASE (e.g. 'IFCWALL'). "
            "dataType must be UPPERCASE IFC type (e.g. 'IFCLENGTHMEASURE'). "
            "Each value field uses {'simpleValue': '...'} or {'restriction': {...}}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ids_json_string": {
                    "type": "string",
                    "description": (
                        "The IDS document as a JSON STRING. Must be valid JSON with "
                        "'info' and 'specifications' keys."
                    ),
                }
            },
            "required": ["ids_json_string"],
        },
    },
    {
        "name": "define_entity",
        "description": (
            "Define a new civil engineering entity in the Universal Civil Knowledge "
            "Schema (UCKS). Takes a structured JSON object describing the entity with "
            "its properties, relationships, and metadata. Validates the definition, "
            "saves it as YAML, and ingests it into the Neo4j knowledge graph. "
            "Use this when users describe building elements, infrastructure components, "
            "facility equipment, or any civil engineering concept."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_json_string": {
                    "type": "string",
                    "description": (
                        "The entity definition as a JSON STRING. Must include: "
                        "'id' (snake_case), 'name', 'description', 'sector' "
                        "(building/infrastructure/facility/urban/general), "
                        "'domain' (structural/architectural/mechanical/etc.), "
                        "and optionally 'parent', 'property_groups', 'relationships'."
                    ),
                }
            },
            "required": ["entity_json_string"],
        },
    },
    {
        "name": "list_ucks_entities",
        "description": (
            "List all UCKS entities currently defined in the knowledge library. "
            "Returns entity id, name, sector, domain, and property/relationship counts. "
            "Use this to check what has already been defined."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_ucks_entity",
        "description": (
            "Get the full details of a UCKS entity from the knowledge library — "
            "all property groups, properties (with types, units, enums), and "
            "relationships. Use this to retrieve a UCKS entity before exporting "
            "it to IFC or another format."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "entity_id": {
                    "type": "string",
                    "description": "The UCKS entity id (snake_case), e.g. 'bridge_pier', 'retaining_wall'",
                }
            },
            "required": ["entity_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

_neo4j_driver: neo4j.Driver | None = None


def _get_neo4j_driver() -> neo4j.Driver:
    global _neo4j_driver
    if _neo4j_driver is None:
        _neo4j_driver = neo4j.GraphDatabase.driver(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
        )
    return _neo4j_driver


def _run_cypher_safe(query: str) -> dict:
    """Execute a read-only Cypher query. Rejects write operations."""
    if _WRITE_KEYWORDS.search(query):
        return {"error": "Write operations are not allowed. Only read queries (MATCH/RETURN) are permitted."}

    try:
        driver = _get_neo4j_driver()
        with driver.session() as session:
            result = session.run(query)
            records = []
            for record in result:
                records.append({k: _serialize(v) for k, v in record.items()})
            return {"records": records, "count": len(records)}
    except Exception as e:
        return {"error": str(e)}


def _serialize(value):
    """Make Neo4j values JSON-serializable."""
    if isinstance(value, (neo4j.graph.Node,)):
        return dict(value)
    if isinstance(value, (neo4j.graph.Relationship,)):
        return {"type": value.type, "properties": dict(value)}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def _truncate_result(result: dict, max_chars: int = 24000) -> dict:
    """Intelligently truncate a large tool result to fit within context limits."""
    # For query_class results: trim property lists within each property set
    if "property_sets" in result and isinstance(result["property_sets"], dict):
        trimmed = {k: v for k, v in result.items() if k != "property_sets"}
        trimmed_psets = {}
        for pset_name, props in result["property_sets"].items():
            if isinstance(props, list) and len(props) > 10:
                trimmed_psets[pset_name] = props[:10] + [
                    {"note": f"... and {len(props) - 10} more properties (truncated)"}
                ]
            else:
                trimmed_psets[pset_name] = props
            # Check size after each pset
            trimmed["property_sets"] = trimmed_psets
            if len(json.dumps(trimmed, default=str, ensure_ascii=False)) > max_chars:
                trimmed_psets[pset_name] = [{"note": f"({len(props)} properties, truncated)"}]
                break
        trimmed["property_sets"] = trimmed_psets
        trimmed["_truncated"] = True
        return trimmed

    # For cypher results: trim records list
    if "records" in result and isinstance(result["records"], list):
        records = result["records"]
        trimmed = dict(result)
        while len(json.dumps(trimmed, default=str, ensure_ascii=False)) > max_chars and len(trimmed["records"]) > 5:
            trimmed["records"] = trimmed["records"][: len(trimmed["records"]) // 2]
        trimmed["_truncated"] = True
        trimmed["_total_records"] = len(records)
        return trimmed

    # Generic fallback: convert to string representation
    return {"summary": json.dumps(result, default=str, ensure_ascii=False)[:max_chars], "_truncated": True}


def dispatch_tool(name: str, args: dict) -> dict:
    """Route tool calls to actual functions."""
    logger.info("Tool call: %s(%s)", name, json.dumps(args, ensure_ascii=False)[:200])

    if name == "query_class":
        return get_class_requirements(args["class_code"])
    elif name == "query_class_structure":
        return get_class_structure(args["class_code"])
    elif name == "search_classes":
        return {"classes": list_classes(args.get("search_term"))}
    elif name == "run_cypher":
        return _run_cypher_safe(args["query"])
    elif name == "generate_ids":
        raw = args.get("ids_json_string") or args.get("ids_json", "{}")
        if isinstance(raw, str):
            try:
                ids_data = json.loads(raw)
            except json.JSONDecodeError as e:
                return {"error": f"Invalid JSON string: {e}"}
        else:
            ids_data = raw
        return generate_ids_from_json(ids_data)
    elif name == "define_entity":
        raw = args.get("entity_json_string") or args.get("entity_json", "{}")
        if isinstance(raw, str):
            try:
                entity_data = json.loads(raw)
            except json.JSONDecodeError as e:
                return {"error": f"Invalid JSON string: {e}"}
        else:
            entity_data = raw
        return define_entity_from_json(entity_data)
    elif name == "list_ucks_entities":
        return {"entities": list_ucks_entities()}
    elif name == "get_ucks_entity":
        return get_ucks_entity_detail(args.get("entity_id", ""))
    else:
        return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# Gemini API call with retry
# ---------------------------------------------------------------------------

def _call_with_retry(client, model, contents, config):
    """Call Gemini generate_content with retry on 429 rate limit errors."""
    for attempt in range(MAX_RETRIES):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                # Extract retry delay from error if available
                delay = RETRY_BASE_DELAY * (attempt + 1)
                import re as _re
                match = _re.search(r"retry in ([\d.]+)s", err_str, _re.IGNORECASE)
                if match:
                    delay = max(int(float(match.group(1))) + 2, delay)

                logger.warning(
                    "Rate limited (attempt %d/%d). Retrying in %ds...",
                    attempt + 1, MAX_RETRIES, delay,
                )
                time.sleep(delay)
            else:
                raise
    # Final attempt — let exception propagate
    return client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(user_message: str, history: list | None = None) -> tuple:
    """
    Run the Gemini agent loop.

    Args:
        user_message: The user's question.
        history: Optional list of prior Content objects for multi-turn chat.

    Returns:
        (answer_text, updated_history, tool_log)
        tool_log is a list of {"tool": name, "args": {...}, "summary": "..."} dicts
    """
    client = genai.Client(api_key=GEMINI_API_KEY)

    tools = types.Tool(function_declarations=TOOL_DECLARATIONS)
    config = types.GenerateContentConfig(
        tools=[tools],
        system_instruction=SYSTEM_PROMPT,
    )

    # Build contents from history + new message
    contents = []
    if history:
        contents.extend(history)

    contents.append(
        types.Content(role="user", parts=[types.Part(text=user_message)])
    )

    tool_log = []

    for iteration in range(MAX_ITERATIONS):
        logger.info("Agent iteration %d/%d", iteration + 1, MAX_ITERATIONS)

        # Call Gemini with retry on rate limits
        response = _call_with_retry(client, GEMINI_MODEL, contents, config)

        candidate = response.candidates[0]

        # Guard against empty content (safety filter, empty response)
        if candidate.content is None or not candidate.content.parts:
            reason = getattr(candidate, 'finish_reason', 'unknown')
            logger.warning("Empty response from Gemini. Finish reason: %s", reason)
            return (
                "I could not generate a response. Please try rephrasing.",
                contents,
                tool_log,
            )

        # Append assistant response to contents
        contents.append(candidate.content)

        # Check for function calls
        fn_calls = [p for p in candidate.content.parts if p.function_call]

        if not fn_calls:
            # No function calls — final text response
            answer = response.text or ""
            return answer, contents, tool_log

        # Execute each function call and build responses
        fn_response_parts = []
        for fc in fn_calls:
            fn_name = fc.function_call.name
            fn_args = dict(fc.function_call.args) if fc.function_call.args else {}

            result = dispatch_tool(fn_name, fn_args)

            # Truncate large results to stay within Gemini context limits
            result_str = json.dumps(result, default=str, ensure_ascii=False)
            if len(result_str) > 25000:
                result = _truncate_result(result)

            # Log tool usage
            summary = fn_name
            if fn_name == "query_class":
                summary = f"Queried {fn_args.get('class_code', '?')}"
            elif fn_name == "query_class_structure":
                summary = f"Queried structure of {fn_args.get('class_code', '?')}"
            elif fn_name == "search_classes":
                summary = f"Searched '{fn_args.get('search_term', '?')}'"
            elif fn_name == "run_cypher":
                summary = "Ran Cypher query"
            elif fn_name == "generate_ids":
                summary = "Generated IDS specification"
            elif fn_name == "define_entity":
                summary = f"Defined UCKS entity"
            elif fn_name == "list_ucks_entities":
                summary = "Listed UCKS entities"
            elif fn_name == "get_ucks_entity":
                summary = f"Retrieved UCKS entity '{fn_args.get('entity_id', '?')}'"

            log_entry = {
                "tool": fn_name,
                "args": fn_args,
                "summary": summary,
            }
            # Capture IDS XML for the frontend
            if fn_name == "generate_ids" and isinstance(result, dict):
                log_entry["ids_xml"] = result.get("ids_xml")

            # Capture UCKS entity for the frontend
            if fn_name == "define_entity" and isinstance(result, dict):
                log_entry["ucks_entity_id"] = result.get("entity_id")
                log_entry["ucks_entity_name"] = result.get("entity_name")
                log_entry["ucks_yaml_path"] = result.get("yaml_saved")

            tool_log.append(log_entry)

            fn_response_parts.append(
                types.Part.from_function_response(
                    name=fn_name,
                    response={"result": result},
                )
            )

        # Send function results back to Gemini
        contents.append(types.Content(role="user", parts=fn_response_parts))

    logger.warning("Agent reached max iterations (%d).", MAX_ITERATIONS)
    return "I reached the maximum number of steps. Please try a simpler question.", contents, tool_log


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="IFC Semantic Query Engine")
    parser.add_argument(
        "--query", "-q",
        default="What property sets does IfcWall require?",
        help="Question to ask about IFC 4.3",
    )
    args = parser.parse_args()

    answer, _, tool_log = run_agent(args.query)
    if tool_log:
        print("--- Tools used ---")
        for t in tool_log:
            print(f"  {t['summary']}")
        print()
    print(answer)


if __name__ == "__main__":
    main()
