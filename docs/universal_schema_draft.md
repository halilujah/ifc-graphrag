# Universal Civil Knowledge Schema (UCKS) — Draft v0.1

> The goal: a clean, minimal format to capture domain knowledge across
> the entire civil engineering spectrum — buildings, infrastructure, facilities — independent
> of any existing standard, that can be exported to IFC, COBie, gbXML, CityGML, LandXML, and others.

## Scope

Unlike IFC (which tries to encode everything in one monolithic schema) or gbXML (which only
covers energy), UCKS captures **core domain knowledge** that spans:

| Sector          | Examples                                      | Existing Standards       |
|-----------------|-----------------------------------------------|--------------------------|
| Buildings       | Walls, slabs, doors, HVAC systems             | IFC, COBie, gbXML        |
| Infrastructure  | Bridges, roads, railways, tunnels             | IFC 4.3, LandXML         |
| Facilities      | Equipment, maintenance zones, asset registers | COBie, Maximo             |
| Urban/GIS       | Sites, parcels, terrain, utilities            | CityGML, InfraGML        |

The schema doesn't try to replace these — it provides a **clean canonical layer** that
captures the essence of each concept, then exports to whichever format is needed.

## Design Principles

1. **Domain-first** — model what things *are*, not how a file format represents them
2. **Sector-agnostic** — same primitives work for a wall, a bridge pier, or a pump
3. **Minimal** — only the primitives needed to express civil engineering knowledge
4. **Graph-native** — maps directly to nodes and edges in a knowledge graph
5. **Human-readable** — YAML as the authoring format, JSON as the interchange format
6. **Export-agnostic** — no IFC-isms, no COBie-isms; exporters handle the translation

---

## Core Primitives

| Primitive         | What it represents                              | Example                          |
|-------------------|------------------------------------------------|----------------------------------|
| **Entity**        | A civil engineering concept / element type     | Wall, BridgeDeck, Pump, Road     |
| **PropertyGroup** | A logical cluster of related properties        | CommonProperties, ThermalProperties, LoadProperties |
| **Property**      | A single measurable/describable attribute      | Thickness, SpanLength, FlowRate  |
| **Relationship**  | How entities relate to each other              | contains, bounds, connects, serves |
| **DataType**      | Value type for a property                      | Real, String, Boolean, Enum      |
| **Enumeration**   | A fixed set of allowed values                  | {INTERNAL, EXTERNAL, NOTDEFINED} |
| **Sector**        | The civil engineering sector                   | Building, Infrastructure, Facility |
| **Domain**        | A knowledge area / discipline within a sector  | Structural, Mechanical, Geotechnical |

---

## Schema Definition (YAML)

### Entity

```yaml
# A building entity
entity:
  id: "wall"
  name: "Wall"
  description: "A vertical element that encloses or divides spaces."
  sector: "building"
  domain: "architectural"
  parent: "building_element"       # inheritance
  property_groups:
    - ref: "common_properties"
    - ref: "thermal_properties"
  relationships:
    - type: "contains"
      target: "opening"
      cardinality: "0..*"
    - type: "bounded_by"
      target: "space"
      cardinality: "0..*"

# An infrastructure entity — same primitives, different sector
entity:
  id: "bridge_deck"
  name: "Bridge Deck"
  description: "The roadway surface of a bridge that carries traffic loads."
  sector: "infrastructure"
  domain: "structural"
  parent: "structural_element"
  property_groups:
    - ref: "structural_properties"
    - ref: "geometric_properties"
  relationships:
    - type: "supported_by"
      target: "bridge_pier"
      cardinality: "1..*"
    - type: "part_of"
      target: "bridge"
      cardinality: "1..1"

# A facility entity — same primitives, different sector
entity:
  id: "pump"
  name: "Pump"
  description: "A mechanical device that moves fluids through a system."
  sector: "facility"
  domain: "mechanical"
  parent: "equipment"
  property_groups:
    - ref: "equipment_common"
    - ref: "pump_performance"
  relationships:
    - type: "serves"
      target: "distribution_system"
      cardinality: "1..*"
    - type: "located_in"
      target: "space"
      cardinality: "1..1"
```

### PropertyGroup

```yaml
property_group:
  id: "common_properties"
  name: "Common Properties"
  description: "Properties shared by most building elements."
  applicable_to:
    - "wall"
    - "slab"
    - "beam"
    - "column"
  properties:
    - ref: "is_external"
    - ref: "fire_rating"
    - ref: "load_bearing"
    - ref: "reference_id"
```

### Property

```yaml
property:
  id: "fire_rating"
  name: "Fire Rating"
  description: "Fire resistance rating of the element."
  data_type: "string"
  unit: null
  constraints:
    pattern: "^REI \\d+$"          # e.g., "REI 60", "REI 120"
  example: "REI 60"

property:
  id: "thermal_transmittance"
  name: "Thermal Transmittance"
  description: "Rate of heat transfer through the element (U-value)."
  data_type: "real"
  unit: "W/(m2*K)"
  constraints:
    min: 0.0
    max: 10.0

property:
  id: "is_external"
  name: "Is External"
  description: "Whether the element is part of the external envelope."
  data_type: "boolean"

property:
  id: "status"
  name: "Status"
  description: "Current status of the element in the project lifecycle."
  data_type: "enum"
  enumeration:
    id: "element_status"
    values: ["NEW", "EXISTING", "DEMOLISH", "TEMPORARY"]
```

### Relationship

```yaml
relationship:
  id: "contains"
  name: "Contains"
  description: "Spatial or physical containment."
  inverse: "contained_in"
  source_types: ["wall", "slab", "space"]
  target_types: ["opening", "element", "space"]
```

---

## Full Examples Across Sectors

### Building: Wall

```yaml
# file: building/wall.yaml
schema: "ucks/0.1"
sector: "building"
domain: "architectural"

entity:
  id: "wall"
  name: "Wall"
  description: "A vertical element that encloses or divides spaces."
  parent: "building_element"

  property_groups:
    - id: "wall_common"
      name: "Wall Common Properties"
      properties:
        - id: "is_external"
          name: "Is External"
          data_type: "boolean"
          required: true

        - id: "load_bearing"
          name: "Load Bearing"
          data_type: "boolean"
          required: true

        - id: "fire_rating"
          name: "Fire Rating"
          data_type: "string"
          required: false
          constraints:
            pattern: "^REI \\d+$"

        - id: "thickness"
          name: "Thickness"
          data_type: "real"
          unit: "mm"
          required: false
          constraints:
            min: 1.0

  relationships:
    - type: "contains"
      target: "opening"
      cardinality: "0..*"
    - type: "bounded_by"
      target: "space"
      cardinality: "0..*"
```

### Infrastructure: Bridge

```yaml
# file: infrastructure/bridge.yaml
schema: "ucks/0.1"
sector: "infrastructure"
domain: "structural"

entity:
  id: "bridge"
  name: "Bridge"
  description: "A structure that spans a physical obstacle (river, road, valley)."
  parent: "civil_structure"

  property_groups:
    - id: "bridge_common"
      name: "Bridge Common Properties"
      properties:
        - id: "total_length"
          name: "Total Length"
          data_type: "real"
          unit: "m"
          required: true

        - id: "design_load_class"
          name: "Design Load Class"
          data_type: "enum"
          required: true
          enumeration:
            id: "load_class"
            values: ["HL-93", "COOPER_E80", "EUROCODE_LM1"]

        - id: "number_of_spans"
          name: "Number of Spans"
          data_type: "integer"
          required: true
          constraints:
            min: 1

        - id: "clearance_below"
          name: "Clearance Below"
          data_type: "real"
          unit: "m"
          required: false

  relationships:
    - type: "composed_of"
      target: "bridge_deck"
      cardinality: "1..*"
    - type: "composed_of"
      target: "bridge_pier"
      cardinality: "0..*"
    - type: "composed_of"
      target: "abutment"
      cardinality: "2..2"
    - type: "spans_over"
      target: "watercourse"
      cardinality: "0..*"
```

### Facility: HVAC Unit

```yaml
# file: facility/air_handling_unit.yaml
schema: "ucks/0.1"
sector: "facility"
domain: "mechanical"

entity:
  id: "air_handling_unit"
  name: "Air Handling Unit"
  description: "Equipment that conditions and circulates air as part of an HVAC system."
  parent: "equipment"

  property_groups:
    - id: "ahu_performance"
      name: "AHU Performance Properties"
      properties:
        - id: "airflow_rate"
          name: "Airflow Rate"
          data_type: "real"
          unit: "m3/h"
          required: true

        - id: "cooling_capacity"
          name: "Cooling Capacity"
          data_type: "real"
          unit: "kW"
          required: false

        - id: "filter_type"
          name: "Filter Type"
          data_type: "enum"
          required: true
          enumeration:
            id: "filter_class"
            values: ["G4", "F7", "F9", "H13", "H14"]

    - id: "ahu_maintenance"
      name: "AHU Maintenance Properties"
      properties:
        - id: "maintenance_interval"
          name: "Maintenance Interval"
          data_type: "integer"
          unit: "days"
          required: false

        - id: "last_serviced"
          name: "Last Serviced"
          data_type: "date"
          required: false

  relationships:
    - type: "serves"
      target: "space"
      cardinality: "1..*"
    - type: "connected_to"
      target: "duct_segment"
      cardinality: "1..*"
    - type: "located_in"
      target: "space"
      cardinality: "1..1"
```

---

## Export Mappings

### UCKS → IFC 4.3

| UCKS Concept     | IFC Equivalent                    | Notes                                    |
|------------------|-----------------------------------|------------------------------------------|
| Entity           | IfcClass (e.g., IfcWall)         | Name mapped via lookup table             |
| PropertyGroup    | Pset_ (PropertySet)              | Prefixed with "Pset_" + entity name      |
| Property         | IfcPropertySingleValue           | Data type mapped to IfcSimpleValue       |
| Relationship     | IfcRelAggregates, IfcRelContains | Mapped by relationship type              |
| DataType: real   | IfcReal / IfcLengthMeasure       | Unit determines specific IFC measure     |
| DataType: string | IfcLabel / IfcText               | Length determines Label vs Text          |
| DataType: boolean| IfcBoolean                       |                                          |
| DataType: enum   | IfcPropertyEnumeratedValue       |                                          |
| parent           | SUPERTYPE OF                     | EXPRESS inheritance chain                |
| Domain           | (no direct equivalent)           | Mapped via IfcClassification             |

### UCKS → COBie

| UCKS Concept     | COBie Equivalent                  | Notes                                    |
|------------------|-----------------------------------|------------------------------------------|
| Entity           | Type / Component sheet row        | Based on whether it's a type or instance |
| PropertyGroup    | (flattened)                       | COBie has no grouping, properties go flat|
| Property         | Attribute sheet row               | One row per property per entity          |
| Relationship     | (implicit)                        | Spaces in Space sheet, zones in Zone     |
| Domain           | Category column                   |                                          |

### UCKS → gbXML

| UCKS Concept     | gbXML Equivalent                  | Notes                                    |
|------------------|-----------------------------------|------------------------------------------|
| Entity (Wall)    | `<Surface surfaceType="ExteriorWall">` | Only energy-relevant entities map |
| Property (U-val) | `<U-value>` element               | Only thermal/energy properties           |
| Relationship     | `<AdjacentSpaceId>`               | Space adjacency only                     |
| Sector/Domain    | (implicit — gbXML is energy only) |                                          |

### UCKS → CityGML

| UCKS Concept     | CityGML Equivalent                | Notes                                    |
|------------------|-----------------------------------|------------------------------------------|
| Entity (Building)| `<bldg:Building>`                 | LOD-dependent geometry detail            |
| Entity (Bridge)  | `<brid:Bridge>`                   | CityGML has bridge module                |
| Entity (Road)    | `<tran:Road>`                     | Transportation module                    |
| Property         | Generic attributes or ADE         | CityGML has limited built-in properties  |
| Relationship     | `<xlink:href>` references         | Topology via shared surfaces             |
| Sector           | CityGML module (bldg, brid, tran) | Maps naturally to CityGML thematic modules|

### UCKS → LandXML

| UCKS Concept     | LandXML Equivalent                | Notes                                    |
|------------------|-----------------------------------|------------------------------------------|
| Entity (Road)    | `<Roadway>`, `<Alignment>`        | Alignment-based infrastructure           |
| Entity (Surface) | `<Surface>`, `<Parcel>`           | Terrain and land parcels                 |
| Property         | Feature attributes                | Varies by element type                   |
| Relationship     | `<CoordGeom>` references          | Geometric connectivity                   |

---

## How It Fits in the Architecture

```
                        ┌─────────────────┐
                        │  Domain Expert   │
                        │  (or AI Agent)   │
                        └────────┬────────┘
                                 │ writes YAML / converses with LLM
                                 ▼
                        ┌─────────────────┐
                        │   UCKS Schema    │
                        │  (YAML / JSON)   │
                        │  "Single Source   │
                        │   of Truth"      │
                        └────────┬────────┘
                                 │
              ┌──────────┬───────┼───────┬──────────┐
              ▼          ▼       ▼       ▼          ▼
         ┌────────┐ ┌───────┐ ┌─────┐ ┌───────┐ ┌───────┐
         │IFC 4.3 │ │COBie  │ │gbXML│ │CityGML│ │LandXML│
         │Exporter│ │Export. │ │Exp. │ │Export. │ │Export. │
         └────────┘ └───────┘ └─────┘ └───────┘ └───────┘
              │          │       │       │          │
              ▼          ▼       ▼       ▼          ▼
           .ifc       .xlsx    .xml    .gml      .xml
                                 │
                    ┌────────────┼────────────┐
                    ▼                         ▼
           ┌──────────────┐         ┌──────────────┐
           │  Neo4j Graph  │         │  GraphRAG    │
           │  (Knowledge   │◄───────│  Query Engine │
           │   Graph)      │         │  (Gemini AI) │
           └──────────────┘         └──────────────┘
```

---

## Graph Model (Neo4j)

When loaded into Neo4j, UCKS becomes:

```
(:Entity {id, name, description, domain})
    -[:INHERITS_FROM]->(:Entity)
    -[:HAS_PROPERTY_GROUP]->(:PropertyGroup {id, name})
        -[:HAS_PROPERTY]->(:Property {id, name, data_type, unit, required})
            -[:HAS_CONSTRAINT]->(:Constraint {type, value})
            -[:HAS_ENUMERATION]->(:Enumeration {id, values})
    -[:RELATES_TO {type, cardinality}]->(:Entity)
```

---

## Why Not Just Use IFC?

IFC 4.3 has ~1,400 classes because it tries to cover everything. The result:
- Massive schema that's hard to implement correctly
- Properties scattered across PropertySets, Attributes, and Type objects
- Software vendors implement subsets differently → data loss during exchange
- Adding a new concept requires an ISO standardization process

UCKS is different:
- **Additive** — a domain expert can define a new entity in 20 lines of YAML
- **Clean** — one entity, its properties, its relationships. No indirection layers
- **Queryable** — the graph structure enables natural language queries out of the box
- **Exportable** — the "dirty" complexity lives in exporters, not in the knowledge itself

## Next Steps

1. **Validate schema design** — review with advisor, iterate on primitives
2. **Define core entities across sectors:**
   - Building: Wall, Slab, Beam, Column, Door, Window, Space, Building, Storey, Roof
   - Infrastructure: Bridge, BridgeDeck, BridgePier, Road, Alignment, Tunnel
   - Facility: Pump, AirHandlingUnit, Sensor, DistributionSystem, MaintenanceZone
3. **Build UCKS → Neo4j ingestion** — load YAML definitions into the graph
4. **Build UCKS → IFC exporter** — generate IFC-compatible output from the graph (first export target)
5. **Integrate with Gemini agent** — add a tool that lets the AI help define new entities conversationally
6. **Build UCKS → COBie exporter** — second export target to prove the pivot works
7. **Cloud library UI** — web interface where users can browse, query, and contribute entity definitions
