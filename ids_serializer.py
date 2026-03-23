"""
Deterministic IDS XML serializer.

Converts an ``IdsDocument`` Pydantic model into a valid IDS XML string.
No LLM involvement — pure code.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ids_models import (
    Applicability,
    AttributeFacet,
    EntityFacet,
    IdsDocument,
    IdsRestriction,
    IdsValue,
    PropertyFacet,
    Requirements,
)

# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------

IDS_NS = "http://standards.buildingsmart.org/IDS"
XS_NS = "http://www.w3.org/2001/XMLSchema"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

# Register prefixes so the output uses ids: / xs: / xsi: instead of ns0/ns1/ns2
ET.register_namespace("ids", IDS_NS)
ET.register_namespace("xs", XS_NS)
ET.register_namespace("xsi", XSI_NS)


def _ids(tag: str) -> str:
    """Return a fully-qualified IDS-namespace tag."""
    return f"{{{IDS_NS}}}{tag}"


def _xs(tag: str) -> str:
    """Return a fully-qualified XS-namespace tag."""
    return f"{{{XS_NS}}}{tag}"


# ---------------------------------------------------------------------------
# Value builders
# ---------------------------------------------------------------------------

def _build_ids_value(parent: ET.Element, tag: str, value: IdsValue) -> None:
    """Append an IDS value element (simpleValue or xs:restriction) under *parent*."""
    wrapper = ET.SubElement(parent, _ids(tag))

    if value.simpleValue is not None:
        sv = ET.SubElement(wrapper, _ids("simpleValue"))
        sv.text = value.simpleValue
    elif value.restriction is not None:
        _build_restriction(wrapper, value.restriction)


def _build_restriction(parent: ET.Element, r: IdsRestriction) -> None:
    """Build an ``<xs:restriction base="...">`` element."""
    restriction = ET.SubElement(parent, _xs("restriction"))
    restriction.set("base", r.base)

    if r.enumerations:
        for val in r.enumerations:
            enum_el = ET.SubElement(restriction, _xs("enumeration"))
            enum_el.set("value", val)

    if r.pattern is not None:
        pat = ET.SubElement(restriction, _xs("pattern"))
        pat.set("value", r.pattern)

    if r.minInclusive is not None:
        el = ET.SubElement(restriction, _xs("minInclusive"))
        el.set("value", r.minInclusive)

    if r.maxInclusive is not None:
        el = ET.SubElement(restriction, _xs("maxInclusive"))
        el.set("value", r.maxInclusive)

    if r.minExclusive is not None:
        el = ET.SubElement(restriction, _xs("minExclusive"))
        el.set("value", r.minExclusive)

    if r.maxExclusive is not None:
        el = ET.SubElement(restriction, _xs("maxExclusive"))
        el.set("value", r.maxExclusive)


# ---------------------------------------------------------------------------
# Facet builders
# ---------------------------------------------------------------------------

def _build_entity(parent: ET.Element, facet: EntityFacet) -> None:
    entity = ET.SubElement(parent, _ids("entity"))
    _build_ids_value(entity, "name", facet.name)
    if facet.predefinedType is not None:
        _build_ids_value(entity, "predefinedType", facet.predefinedType)


def _build_property(
    parent: ET.Element, facet: PropertyFacet, is_requirement: bool
) -> None:
    prop = ET.SubElement(parent, _ids("property"))
    if facet.dataType:
        prop.set("dataType", facet.dataType)
    if is_requirement and facet.cardinality != "required":
        prop.set("cardinality", facet.cardinality)
    if is_requirement and facet.instructions:
        prop.set("instructions", facet.instructions)

    _build_ids_value(prop, "propertySet", facet.propertySet)
    _build_ids_value(prop, "baseName", facet.baseName)
    if facet.value is not None:
        _build_ids_value(prop, "value", facet.value)


def _build_attribute(
    parent: ET.Element, facet: AttributeFacet, is_requirement: bool
) -> None:
    attr = ET.SubElement(parent, _ids("attribute"))
    if is_requirement and facet.cardinality != "required":
        attr.set("cardinality", facet.cardinality)
    if is_requirement and facet.instructions:
        attr.set("instructions", facet.instructions)

    _build_ids_value(attr, "name", facet.name)
    if facet.value is not None:
        _build_ids_value(attr, "value", facet.value)


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_applicability(parent: ET.Element, app: Applicability) -> None:
    el = ET.SubElement(parent, _ids("applicability"))
    if app.minOccurs is not None:
        el.set("minOccurs", str(app.minOccurs))
    if app.maxOccurs is not None:
        el.set("maxOccurs", app.maxOccurs)

    if app.entity is not None:
        _build_entity(el, app.entity)


def _build_requirements(parent: ET.Element, req: Requirements) -> None:
    el = ET.SubElement(parent, _ids("requirements"))

    if req.attributes:
        for attr in req.attributes:
            _build_attribute(el, attr, is_requirement=True)

    if req.properties:
        for prop in req.properties:
            _build_property(el, prop, is_requirement=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def serialize_ids(doc: IdsDocument) -> str:
    """
    Serialize an ``IdsDocument`` to a valid IDS XML string.

    Returns:
        A UTF-8 XML string with ``<?xml ...?>`` declaration.
    """
    root = ET.Element(_ids("ids"))
    root.set(f"{{{XSI_NS}}}schemaLocation",
             f"{IDS_NS} http://standards.buildingsmart.org/IDS/1.0/ids.xsd")

    # --- info ---
    info_el = ET.SubElement(root, _ids("info"))
    title_el = ET.SubElement(info_el, _ids("title"))
    title_el.text = doc.info.title

    for field_name in ("copyright", "version", "description", "author",
                       "date", "purpose", "milestone"):
        val = getattr(doc.info, field_name)
        if val is not None:
            child = ET.SubElement(info_el, _ids(field_name))
            child.text = val

    # --- specifications ---
    specs_el = ET.SubElement(root, _ids("specifications"))

    for spec in doc.specifications:
        spec_el = ET.SubElement(specs_el, _ids("specification"))
        spec_el.set("name", spec.name)
        spec_el.set("ifcVersion", spec.ifcVersion)
        if spec.description:
            spec_el.set("description", spec.description)
        if spec.instructions:
            spec_el.set("instructions", spec.instructions)

        _build_applicability(spec_el, spec.applicability)

        if spec.requirements is not None:
            _build_requirements(spec_el, spec.requirements)

    # Serialize to string
    ET.indent(root, space="\t")
    tree = ET.ElementTree(root)
    # Use xml_declaration with encoding
    import io
    buf = io.BytesIO()
    tree.write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue().decode("UTF-8")
