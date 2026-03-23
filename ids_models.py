"""
Pydantic models for the IDS (Information Delivery Specification) intermediate
JSON schema.  The LLM produces JSON matching these models; a deterministic
serializer then converts them to IDS XML.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

class IdsRestriction(BaseModel):
    """XSD restriction — constrains a value with enumerations, patterns, or ranges."""
    base: str = Field(
        default="xs:string",
        description="XSD base type, e.g. 'xs:string' or 'xs:double'",
    )
    enumerations: list[str] | None = Field(
        default=None,
        description="List of allowed values (xs:enumeration)",
    )
    pattern: str | None = Field(
        default=None,
        description="Regex pattern (xs:pattern)",
    )
    minInclusive: str | None = Field(default=None)
    maxInclusive: str | None = Field(default=None)
    minExclusive: str | None = Field(default=None)
    maxExclusive: str | None = Field(default=None)


class IdsValue(BaseModel):
    """
    An IDS value — either a simple literal or an XSD restriction.
    Exactly one of ``simpleValue`` or ``restriction`` must be set.
    """
    simpleValue: str | None = Field(
        default=None,
        description="A literal string value",
    )
    restriction: IdsRestriction | None = Field(
        default=None,
        description="An XSD restriction (enumeration, pattern, range)",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> "IdsValue":
        has_simple = self.simpleValue is not None
        has_restriction = self.restriction is not None
        if has_simple == has_restriction:
            raise ValueError(
                "Exactly one of 'simpleValue' or 'restriction' must be set, "
                f"got simpleValue={'set' if has_simple else 'unset'}, "
                f"restriction={'set' if has_restriction else 'unset'}"
            )
        return self


# ---------------------------------------------------------------------------
# Facets
# ---------------------------------------------------------------------------

class EntityFacet(BaseModel):
    """IDS entity facet — filters by IFC entity type."""
    name: IdsValue = Field(description="Entity name, UPPERCASE (e.g. 'IFCWALL')")
    predefinedType: IdsValue | None = Field(
        default=None,
        description="Optional predefined type filter",
    )


class PropertyFacet(BaseModel):
    """IDS property facet — requires/filters a property."""
    propertySet: IdsValue = Field(description="Property set name (e.g. 'Pset_WallCommon')")
    baseName: IdsValue = Field(description="Property name within the set")
    value: IdsValue | None = Field(default=None, description="Expected value constraint")
    dataType: str | None = Field(
        default=None,
        description="UPPERCASE IFC data type (e.g. 'IFCTEXT', 'IFCBOOLEAN', 'IFCLENGTHMEASURE')",
    )
    cardinality: str = Field(
        default="required",
        description="'required', 'prohibited', or 'optional' (only for requirements)",
    )
    instructions: str | None = Field(default=None)


class AttributeFacet(BaseModel):
    """IDS attribute facet — requires/filters an IFC attribute."""
    name: IdsValue = Field(description="Attribute name (e.g. 'Name', 'Description')")
    value: IdsValue | None = Field(default=None, description="Expected value constraint")
    cardinality: str = Field(
        default="required",
        description="'required', 'prohibited', or 'optional' (only for requirements)",
    )
    instructions: str | None = Field(default=None)


# ---------------------------------------------------------------------------
# Applicability & Requirements
# ---------------------------------------------------------------------------

class Applicability(BaseModel):
    """Defines which IFC objects a specification applies to."""
    entity: EntityFacet | None = Field(default=None)
    minOccurs: int | None = Field(default=None)
    maxOccurs: str | None = Field(
        default=None,
        description="'unbounded' or an integer string",
    )


class Requirements(BaseModel):
    """Defines what must/must-not be present on matching objects."""
    properties: list[PropertyFacet] | None = Field(default=None)
    attributes: list[AttributeFacet] | None = Field(default=None)


# ---------------------------------------------------------------------------
# Specification & Document
# ---------------------------------------------------------------------------

class Specification(BaseModel):
    """A single IDS specification (applicability + requirements)."""
    name: str = Field(description="Human-readable specification name")
    ifcVersion: str = Field(
        default="IFC4X3_ADD2",
        description="Space-separated IFC versions (e.g. 'IFC4X3_ADD2', 'IFC2X3 IFC4')",
    )
    description: str | None = Field(default=None)
    instructions: str | None = Field(default=None)
    applicability: Applicability
    requirements: Requirements | None = Field(default=None)


class IdsInfo(BaseModel):
    """Metadata for the IDS document."""
    title: str
    description: str | None = Field(default=None)
    copyright: str | None = Field(default=None)
    version: str | None = Field(default=None)
    author: str | None = Field(default=None, description="Email address")
    date: str | None = Field(default=None, description="ISO date YYYY-MM-DD")
    purpose: str | None = Field(default=None)
    milestone: str | None = Field(default=None)


class IdsDocument(BaseModel):
    """Top-level IDS document — the root object the LLM must produce."""
    info: IdsInfo
    specifications: list[Specification] = Field(min_length=1)
