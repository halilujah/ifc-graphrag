"""
UCKS (Universal Civil Knowledge Schema) — Pydantic models.

These models define the canonical format for civil engineering domain knowledge.
The LLM produces JSON matching these models; a pipeline then serializes to YAML
and ingests into Neo4j.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Property-level models
# ---------------------------------------------------------------------------

class Constraint(BaseModel):
    """Value constraint on a property."""
    min: float | None = Field(default=None, description="Minimum value (inclusive)")
    max: float | None = Field(default=None, description="Maximum value (inclusive)")
    pattern: str | None = Field(default=None, description="Regex pattern for string values")


class EnumerationDef(BaseModel):
    """A fixed set of allowed values."""
    id: str = Field(description="Enumeration identifier, e.g. 'element_status'")
    values: list[str] = Field(min_length=1, description="Allowed values")


class PropertyDef(BaseModel):
    """A single measurable or describable attribute of an entity."""
    id: str = Field(description="Unique property identifier, e.g. 'fire_rating'")
    name: str = Field(description="Human-readable name, e.g. 'Fire Rating'")
    description: str | None = Field(default=None, description="What this property represents")
    data_type: str = Field(description="One of: string, real, integer, boolean, enum, date")
    unit: str | None = Field(default=None, description="Measurement unit, e.g. 'mm', 'kW', 'W/(m2*K)'")
    required: bool = Field(default=False, description="Whether this property is mandatory")
    constraints: Constraint | None = Field(default=None, description="Value constraints")
    enumeration: EnumerationDef | None = Field(default=None, description="Allowed values (when data_type is 'enum')")
    example: str | None = Field(default=None, description="Example value")


# ---------------------------------------------------------------------------
# PropertyGroup
# ---------------------------------------------------------------------------

class PropertyGroupDef(BaseModel):
    """A logical cluster of related properties."""
    id: str = Field(description="Unique group identifier, e.g. 'wall_common'")
    name: str = Field(description="Human-readable name, e.g. 'Wall Common Properties'")
    description: str | None = Field(default=None)
    properties: list[PropertyDef] = Field(min_length=1, description="Properties in this group")


# ---------------------------------------------------------------------------
# Relationship
# ---------------------------------------------------------------------------

class RelationshipDef(BaseModel):
    """How this entity relates to another entity."""
    type: str = Field(description="Relationship type, e.g. 'contains', 'supported_by', 'serves'")
    target: str = Field(description="Target entity id, e.g. 'opening', 'bridge_pier'")
    cardinality: str = Field(default="0..*", description="e.g. '1..1', '0..*', '1..*', '2..2'")
    description: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Entity — the core primitive
# ---------------------------------------------------------------------------

VALID_SECTORS = {"building", "infrastructure", "facility", "urban", "general"}

VALID_DOMAINS = {
    "architectural", "structural", "mechanical", "electrical", "plumbing",
    "geotechnical", "transportation", "hydraulic", "environmental",
    "fire_protection", "telecommunications", "general",
}

VALID_DATA_TYPES = {"string", "real", "integer", "boolean", "enum", "date"}


class EntityDef(BaseModel):
    """
    A civil engineering concept — the core UCKS primitive.

    Examples: Wall, Bridge, Pump, Road, AirHandlingUnit, Tunnel
    """
    id: str = Field(description="Unique entity identifier, snake_case, e.g. 'bridge_deck'")
    name: str = Field(description="Human-readable name, e.g. 'Bridge Deck'")
    description: str = Field(description="Clear, concise definition of what this entity is")
    sector: str = Field(description="One of: building, infrastructure, facility, urban, general")
    domain: str = Field(description="Discipline, e.g. architectural, structural, mechanical, geotechnical")
    parent: str | None = Field(default=None, description="Parent entity id for inheritance, e.g. 'structural_element'")
    property_groups: list[PropertyGroupDef] = Field(default_factory=list, description="Property groups")
    relationships: list[RelationshipDef] = Field(default_factory=list, description="Relationships to other entities")


# ---------------------------------------------------------------------------
# Document wrapper (for batch definitions)
# ---------------------------------------------------------------------------

class UcksDocument(BaseModel):
    """A UCKS document containing one or more entity definitions."""
    schema_version: str = Field(default="ucks/0.1")
    entities: list[EntityDef] = Field(min_length=1)
