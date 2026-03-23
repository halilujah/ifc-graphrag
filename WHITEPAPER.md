# IFC GraphRAG: A Graph-Based Retrieval-Augmented Generation Engine for the IFC 4.3 Standard

**Authors:** Halil et al.
**Date:** March 2026
**Version:** 1.0

---

## Abstract

The Industry Foundation Classes (IFC) standard is the backbone of open BIM interoperability, yet its sheer scale — hundreds of entity types, thousands of properties, and a deeply nested inheritance hierarchy — makes it difficult for practitioners to query, understand, and enforce. We present **IFC GraphRAG**, a system that combines a Neo4j knowledge graph with a Gemini-powered agentic LLM to provide natural-language access to the full IFC 4.3 schema. The system ingests both the buildingSMART Data Dictionary (bSDD) JSON export and the IFC4X3 ADD2 EXPRESS schema into a unified property graph, then exposes five specialized tools to an LLM agent that can answer structural questions, retrieve property requirements, execute arbitrary graph traversals, and — critically — generate validated Information Delivery Specification (IDS) XML files on demand. This paper describes the architecture, knowledge graph schema, agent design, IDS generation pipeline, and web interface.

---

## 1. Introduction

### 1.1 The Problem

The IFC 4.3 standard defines over 900 entity types, each with explicit attributes (from the EXPRESS schema), inherited attributes (via deep inheritance chains), property sets (from the bSDD), and associated data types, enumerations, and select types. Practitioners face several challenges:

1. **Discovery:** Finding the correct entity, property set, or attribute for a given domain concept requires navigating multiple specifications and data sources.
2. **Structural understanding:** Understanding how entities relate — placement chains, shape representations, inverse relationships — demands reading the EXPRESS schema directly.
3. **Compliance specification:** Authoring IDS files (the buildingSMART standard for defining information delivery requirements) is a manual, error-prone process that requires exact knowledge of entity names, property set names, property names, and data types.
4. **Cross-referencing:** Answering questions like "which entities reference IfcProduct?" or "what property sets do IfcWall and IfcSlab share?" requires traversals across both the property and structural layers.

### 1.2 Our Approach

IFC GraphRAG addresses these challenges by:

- Constructing a **dual-layer knowledge graph** in Neo4j that unifies bSDD property data with EXPRESS structural definitions.
- Wrapping this graph with a **tool-augmented LLM agent** (Gemini) that reasons over graph data to answer natural-language questions.
- Providing an **IDS generation pipeline** where the LLM produces structured JSON (validated by Pydantic), which is deterministically serialized to XML and validated against the IDS XSD schema.
- Delivering all capabilities through a **web interface** with interactive graph visualization, chat, and IDS file download.

---

## 2. Knowledge Graph Construction

The knowledge graph is built in two phases from two complementary data sources.

### 2.1 Phase 1: bSDD JSON Ingestion

The first phase ingests the `ifc-4.3.json` file exported from the buildingSMART Data Dictionary. This file contains Class entries, GroupOfProperties (PropertySet) entries, and Property definitions.

**Node types created:**

| Node Label | Key Field | Description |
|---|---|---|
| `Class` | `code` | IFC entity (e.g., IfcWall, IfcBridge) |
| `PropertySet` | `code` | Group of properties (e.g., Pset_WallCommon) |
| `Property` | `code` | Individual property with data type and allowed values |

**Relationships created:**

| Relationship | From | To | Description |
|---|---|---|---|
| `HAS_PROPERTY_SET` | Class | PropertySet | Class requires this property set |
| `HAS_PROPERTY` | PropertySet | Property | Property set contains this property |
| `INHERITS_FROM` | Class | Class | Parent class in bSDD hierarchy |

Properties carry rich metadata: `data_type` (String, Real, Boolean, Integer), `property_value_kind` (Single, Enumerated, etc.), and `allowed_values` (a JSON-serialized list of permitted values with codes and descriptions).

### 2.2 Phase 2: EXPRESS Schema Enrichment

The second phase parses the `IFC4X3_ADD2.exp.txt` EXPRESS schema file using a custom regex-based parser that extracts ENTITY, TYPE, ENUMERATION, and SELECT definitions.

**Additional node types:**

| Node Label | Key Field | Description |
|---|---|---|
| `Attribute` | `qualified_name` | Entity attribute (e.g., IfcWall.PredefinedType) |
| `Type` | `name` | Simple or aggregate type (e.g., IfcLabel, IfcLengthMeasure) |
| `Enumeration` | `name` | Enumeration type (e.g., IfcWallTypeEnum) |
| `EnumValue` | `qualified_name` | Individual enum value (e.g., IfcWallTypeEnum.PARTITIONING) |
| `SelectType` | `name` | SELECT type (e.g., IfcActorSelect) |

**Additional relationships:**

| Relationship | From | To | Description |
|---|---|---|---|
| `HAS_ATTRIBUTE` | Class | Attribute | Entity declares this attribute |
| `ATTRIBUTE_TYPE` | Attribute | Class/Type/Enumeration/SelectType | The type of the attribute |
| `REFERS_TO_CLASS` | Attribute | Class | Attribute points to another entity |
| `HAS_VALUE` | Enumeration | EnumValue | Enumeration contains this value |
| `HAS_OPTION` | SelectType | Class/Type/Enumeration/SelectType | SELECT type option |

The EXPRESS parser handles explicit attributes (with optionality, aggregate kinds, and bounds), INVERSE attributes, DERIVE attributes, WHERE validation rules, and ABSTRACT/SUBTYPE declarations. Attributes carry positional ordering and declare their originating entity, enabling accurate reconstruction of the full attribute list across inheritance chains.

### 2.3 Graph Unification

The two phases share the `Class` node as their join point — entities from the EXPRESS schema are `MERGE`d into existing Class nodes from Phase 1, enriching them with `abstract` and `where_rules` fields. EXPRESS inheritance relationships are also merged, validating and augmenting the bSDD hierarchy. The result is a single unified graph where a query about `IfcWall` can return both its property sets (from bSDD) and its explicit attributes, type chain, and placement structure (from EXPRESS).

### 2.4 Ingestion Performance

All node and relationship creation uses batched `UNWIND` queries (batch size 500) with `MERGE` for idempotency. Uniqueness constraints and indexes are created upfront on key fields. The full ingestion completes in seconds on a standard machine.

---

## 3. Agent Architecture

### 3.1 Overview

The system uses Google Gemini (configurable model, default `gemini-2.5-flash`) as the reasoning engine, connected to the knowledge graph via Gemini's function calling API. The agent operates in an iterative loop (up to 10 iterations) where each iteration may include one or more tool calls, with results fed back to the model for further reasoning.

### 3.2 Tool Suite

The LLM has access to five tools:

#### 3.2.1 `query_class`
Retrieves all PropertySets and Properties for a given IFC class. Returns the class definition, parent class, and a nested structure of property sets with their properties (including data types and allowed values). This is the primary tool for property-related questions.

#### 3.2.2 `query_class_structure`
Retrieves the EXPRESS structural definition of an IFC class: explicit attributes with types and optionality, inverse attributes, the full inheritance chain (via variable-length path traversal), and WHERE validation rules. This is the primary tool for structural questions about placement, representation, and entity dependencies.

#### 3.2.3 `search_classes`
Performs case-insensitive keyword search across class codes and names. Returns matching classes with code, name, and definition. This tool enables the LLM to discover classes before querying them in detail.

#### 3.2.4 `run_cypher`
Executes arbitrary read-only Cypher queries against the Neo4j graph. Write operations (CREATE, DELETE, SET, MERGE, DROP, etc.) are blocked by a regex guard. This tool enables aggregations, comparisons, traversals, and complex multi-node questions that cannot be answered by the other tools.

#### 3.2.5 `generate_ids`
Generates an IDS XML file from a structured JSON specification. The LLM is instructed to first use `query_class` to retrieve exact property set names, property names, and data types, then construct a JSON document matching the `IdsDocument` Pydantic schema. The JSON is validated, serialized to XML, and validated against the IDS XSD schema.

### 3.3 System Prompt Design

The system prompt provides the LLM with:

1. **Full graph schema documentation** — both the property layer and structural layer, with node labels, properties, and relationship types.
2. **Tool selection guidelines** — which tool to use for which type of question.
3. **IDS generation workflow** — a step-by-step protocol: (1) search for the class, (2) query exact property names and types, (3) construct and submit the IDS JSON.
4. **Data type mapping** — the mapping from bSDD data types (String, Real, Boolean) to IFC data types (IFCTEXT, IFCREAL, IFCBOOLEAN) required by IDS.
5. **Formatting rules** — entity names must be UPPERCASE in IDS, each value field uses `simpleValue` or `restriction` (not both), and validation errors should be fixed and retried.

### 3.4 Result Management

Tool results are JSON-serialized and checked against a 25,000-character threshold. Large results are intelligently truncated:

- **Property results:** Property lists within each property set are trimmed to 10 entries, with remaining counts noted.
- **Cypher results:** Record lists are halved iteratively until under the threshold.
- **Generic results:** Stringified and truncated with a flag.

This ensures the agent stays within Gemini's context limits while preserving the most useful information.

### 3.5 Conversation Management

The agent supports multi-turn conversations by passing Gemini `Content` objects as history. Each tool call and response is appended to the conversation, enabling follow-up questions and iterative refinement. Sessions are keyed by a random ID and stored in memory.

### 3.6 Rate Limit Handling

The system includes exponential backoff retry logic for Gemini 429 (RESOURCE_EXHAUSTED) errors, with delay extraction from error messages when available. Up to 3 retries are attempted before propagating the error.

---

## 4. IDS Generation Pipeline

A key capability of the system is the ability to generate buildingSMART Information Delivery Specification (IDS) files through natural language. The pipeline has three stages, each providing a validation gate.

### 4.1 Stage 1: Pydantic Schema Validation

The LLM produces a JSON string that is validated against a strict Pydantic model hierarchy:

```
IdsDocument
  ├── IdsInfo (title, description, copyright, version, author, date, purpose, milestone)
  └── Specification[] (min 1)
        ├── name, ifcVersion, description, instructions
        ├── Applicability
        │     ├── EntityFacet (name: IdsValue, predefinedType?: IdsValue)
        │     ├── minOccurs?, maxOccurs?
        └── Requirements?
              ├── PropertyFacet[] (propertySet, baseName, value?, dataType?, cardinality)
              └── AttributeFacet[] (name, value?, cardinality)
```

The `IdsValue` type enforces mutual exclusivity: exactly one of `simpleValue` (literal string) or `restriction` (XSD restriction with enumerations, patterns, or ranges) must be set.

**Supported XSD restriction facets:**
- `xs:enumeration` — list of allowed values
- `xs:pattern` — regex pattern matching
- `xs:minInclusive`, `xs:maxInclusive` — inclusive range bounds
- `xs:minExclusive`, `xs:maxExclusive` — exclusive range bounds
- Configurable `base` type (xs:string, xs:double, etc.)

If Pydantic validation fails, structured error messages (with field paths) are returned to the LLM, which can fix the JSON and retry.

### 4.2 Stage 2: Deterministic XML Serialization

The validated Pydantic model is serialized to IDS XML by a deterministic, code-only serializer (no LLM involvement). The serializer:

- Uses the IDS namespace (`http://standards.buildingsmart.org/IDS`) with proper `ids:`, `xs:`, and `xsi:` prefixes.
- Constructs the XML tree using Python's `xml.etree.ElementTree`.
- Handles all facet types: entity, property, and attribute facets with their value types.
- Emits `cardinality` and `instructions` attributes only on requirement facets.
- Produces formatted output with tab indentation and UTF-8 XML declaration.

### 4.3 Stage 3: XSD Schema Validation

The generated XML is validated against the official IDS XSD schema using `lxml`. The validator:

- Loads and caches the compiled XSD schema on first use.
- Falls back gracefully (optimistic pass) if `lxml` is unavailable or the XSD cannot be loaded (e.g., due to network issues with imported schemas).
- Returns structured error messages on validation failure, which are passed back to the LLM for correction.

### 4.4 Supported IDS Facets

| Facet | Applicability | Requirements | Value Types |
|---|---|---|---|
| Entity | Name, PredefinedType | — | simpleValue, restriction |
| Property | — | PropertySet, BaseName, Value, DataType | simpleValue, restriction |
| Attribute | — | Name, Value | simpleValue, restriction |

Cardinality options for requirements: `required`, `optional`, `prohibited`.

### 4.5 Error Recovery

The pipeline is designed for LLM-in-the-loop error recovery. When any stage fails, the error information is structured and returned as a tool result. The system prompt instructs the LLM to examine the errors, fix its JSON, and retry. The agent loop allows up to 10 iterations, giving the model multiple attempts to produce valid output.

---

## 5. Web Interface

The system is served as a Flask web application with three main components:

### 5.1 Chat Interface

A chat panel on the left side of the interface allows natural-language interaction with the agent. Features include:

- Multi-turn conversation with session management
- Tool usage display (pill badges showing which tools were invoked)
- Clickable IFC class names in responses (loads the graph visualization)
- Markdown rendering (bold, code, lists, paragraphs)
- Session reset capability

### 5.2 Graph Visualization

The center panel renders interactive knowledge graph visualizations using vis.js with force-directed layout (ForceAtlas2). Features include:

- **Color-coded node groups:** Classes (blue), PropertySets (green), Properties (amber), Parent classes (purple), Attributes, Types, Enumerations, SelectTypes
- **Rich tooltips:** Definitions, data types, allowed values, optionality
- **Navigation:** Double-click a class or parent node to navigate to its graph
- **Both layers visualized:** A single class view shows property sets, properties, attributes, and type targets together
- **Node details panel:** Click any node to see its full metadata in a side panel

### 5.3 IDS Preview and Download

When the agent generates an IDS file, the chat interface renders a syntax-highlighted XML preview with:

- Collapsible XML view
- Copy to clipboard
- Download as `.ids` file
- Server-side persistence to `data/ids_output/` with timestamped filenames

### 5.4 API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Main web interface |
| `/api/classes` | GET | Search classes by keyword |
| `/api/graph/<class_code>` | GET | Get vis.js graph data for a class |
| `/api/graph/overview` | GET | High-level class-to-PropertySet overview graph |
| `/api/node/<type>/<code>` | GET | Full node details |
| `/api/chat` | POST | Chat with the agent (message + session_id) |
| `/api/chat/reset` | POST | Reset a chat session |

---

## 6. Technology Stack

| Component | Technology |
|---|---|
| Knowledge Graph | Neo4j 5.x |
| LLM | Google Gemini (2.5 Flash, configurable) |
| Schema Validation | Pydantic 2.x |
| XML Processing | xml.etree.ElementTree (serialization), lxml (XSD validation) |
| Web Framework | Flask 3.x |
| Graph Visualization | vis.js (vis-network) |
| EXPRESS Parsing | Custom regex-based parser |
| Configuration | python-dotenv |

---

## 7. Example Workflows

### 7.1 Property Discovery

**User:** "What properties does IfcBridge need?"

1. Agent calls `query_class("IfcBridge")`
2. Neo4j returns class definition, parent class, and all property sets with properties
3. Agent formats a structured response grouped by property set

### 7.2 Structural Query

**User:** "How is IfcWall placed in space? What is its placement chain?"

1. Agent calls `query_class_structure("IfcWall")`
2. Neo4j returns attributes including `ObjectPlacement` (type: IfcObjectPlacement), inheritance chain up to IfcRoot
3. Agent may follow up with `query_class_structure("IfcLocalPlacement")` to trace the full chain

### 7.3 Cross-Entity Analysis

**User:** "Which entities reference IfcProduct?"

1. Agent calls `run_cypher("MATCH (a:Attribute)-[:REFERS_TO_CLASS]->(c:Class {code: 'IfcProduct'}) RETURN a.declaring_entity, a.name ORDER BY a.declaring_entity")`
2. Neo4j returns all attributes across the schema that reference IfcProduct
3. Agent summarizes the referencing entities and their attribute names

### 7.4 IDS Generation

**User:** "Generate an IDS specification requiring all walls to have the IsExternal property and a Name attribute."

1. Agent calls `search_classes("wall")` to find `IfcWall`
2. Agent calls `query_class("IfcWall")` to get exact property set and property names
3. Agent constructs the IDS JSON with entity `IFCWALL`, property `Pset_WallCommon.IsExternal`, and attribute `Name`
4. Agent calls `generate_ids(...)` with the JSON
5. Pipeline validates with Pydantic, serializes to XML, validates against XSD
6. Valid IDS XML is returned and displayed in the chat with download option

---

## 8. Design Decisions

### 8.1 Dual-Source Ingestion

The bSDD JSON and EXPRESS schema provide complementary information. The bSDD contains property-level semantics (data types, allowed values, human-readable definitions), while EXPRESS contains structural-level semantics (attributes, type systems, inheritance, validation rules). Unifying both into a single graph enables questions that span both domains.

### 8.2 Deterministic XML Serialization

We deliberately keep the LLM out of XML generation. The LLM produces structured JSON (which it handles well), and a deterministic serializer converts this to XML (which requires precise namespace handling and schema compliance). This separation makes the pipeline more reliable and auditable.

### 8.3 Three-Stage Validation

Each validation stage catches different classes of errors:
- **Pydantic:** Structural errors (missing fields, wrong types, mutual exclusivity violations)
- **Serializer:** Logic errors (impossible combinations)
- **XSD:** Schema compliance errors (ordering, namespace, content model violations)

Returning errors to the LLM at each stage enables self-correction without human intervention.

### 8.4 Read-Only Cypher

The `run_cypher` tool is guarded against write operations to protect the knowledge graph's integrity. This allows the LLM to execute powerful traversals while preventing accidental or adversarial modifications.

---

## 9. Limitations and Future Work

1. **Property inheritance:** The current bSDD ingestion does not recursively resolve inherited property sets. A class only shows property sets directly assigned to it, not those inherited from parent classes.
2. **EXPRESS DERIVE attributes:** Derived attributes are parsed but not ingested into the graph. Adding them would enable questions about computed properties.
3. **IDS facet coverage:** The current IDS pipeline supports Entity, Property, and Attribute facets. Material and Classification facets are not yet supported.
4. **Scalability:** Chat session history is stored in-memory. A production deployment would require persistent storage and session cleanup.
5. **Multi-model support:** The system currently targets IFC 4.3 only. Supporting multiple IFC versions simultaneously would require version-tagged nodes or separate graphs.
6. **Validation feedback loop:** While the LLM can retry on validation errors, there is no mechanism to learn from repeated failures across sessions.

---

## 10. Conclusion

IFC GraphRAG demonstrates that combining a structured knowledge graph with a tool-augmented LLM agent can make the IFC standard genuinely accessible through natural language. The system's ability to generate validated IDS files from conversational requests — grounding every entity name, property set, and data type in the actual schema graph — represents a practical step toward AI-assisted BIM compliance specification. The dual-layer graph design, deterministic serialization pipeline, and multi-stage validation architecture provide a foundation that can be extended to support additional IDS facets, multiple IFC versions, and integration with model checking workflows.

---

## Appendix A: Graph Schema Summary

```
(:Class {code, name, definition, parent_class_code, uid, abstract, where_rules})
(:PropertySet {code, name, definition})
(:Property {code, name, definition, data_type, property_value_kind, allowed_values})
(:Attribute {qualified_name, name, optional, aggregate_kind, bounds, raw_type, type_ref, declaring_entity, position, is_inverse, for_attribute})
(:Type {name, kind, underlying_type, aggregate_kind, bounds, element_type, where_rules})
(:Enumeration {name})
(:EnumValue {qualified_name, value, enumeration_name})
(:SelectType {name, options})

(:Class)-[:HAS_PROPERTY_SET]->(:PropertySet)
(:PropertySet)-[:HAS_PROPERTY]->(:Property)
(:Class)-[:INHERITS_FROM]->(:Class)
(:Class)-[:HAS_ATTRIBUTE]->(:Attribute)
(:Attribute)-[:ATTRIBUTE_TYPE]->(:Class | :Type | :Enumeration | :SelectType)
(:Attribute)-[:REFERS_TO_CLASS]->(:Class)
(:Enumeration)-[:HAS_VALUE]->(:EnumValue)
(:SelectType)-[:HAS_OPTION]->(:Class | :Type | :Enumeration | :SelectType)
```

## Appendix B: IDS JSON Schema (Pydantic)

```
IdsDocument
├── info: IdsInfo
│   ├── title: str (required)
│   ├── description: str?
│   ├── copyright: str?
│   ├── version: str?
│   ├── author: str? (email)
│   ├── date: str? (ISO YYYY-MM-DD)
│   ├── purpose: str?
│   └── milestone: str?
└── specifications: Specification[] (min 1)
    ├── name: str (required)
    ├── ifcVersion: str (default "IFC4X3_ADD2")
    ├── description: str?
    ├── instructions: str?
    ├── applicability: Applicability
    │   ├── entity: EntityFacet?
    │   │   ├── name: IdsValue (UPPERCASE, e.g. "IFCWALL")
    │   │   └── predefinedType: IdsValue?
    │   ├── minOccurs: int?
    │   └── maxOccurs: str? ("unbounded" or integer)
    └── requirements: Requirements?
        ├── properties: PropertyFacet[]?
        │   ├── propertySet: IdsValue
        │   ├── baseName: IdsValue
        │   ├── value: IdsValue?
        │   ├── dataType: str? (UPPERCASE, e.g. "IFCBOOLEAN")
        │   ├── cardinality: str (required|optional|prohibited)
        │   └── instructions: str?
        └── attributes: AttributeFacet[]?
            ├── name: IdsValue
            ├── value: IdsValue?
            ├── cardinality: str (required|optional|prohibited)
            └── instructions: str?

IdsValue (exactly one must be set):
├── simpleValue: str?
└── restriction: IdsRestriction?
    ├── base: str (default "xs:string")
    ├── enumerations: str[]?
    ├── pattern: str?
    ├── minInclusive: str?
    ├── maxInclusive: str?
    ├── minExclusive: str?
    └── maxExclusive: str?
```
