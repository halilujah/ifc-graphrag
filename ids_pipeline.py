"""
IDS generation pipeline — orchestrates model validation, XML serialization,
and XSD validation.

Called from ``dispatch_tool()`` in ``main_orchestrator.py`` when the LLM
invokes the ``generate_ids`` tool.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError

from ids_models import IdsDocument
from ids_serializer import serialize_ids
from ids_validator import validate_ids_xml

logger = logging.getLogger(__name__)


def generate_ids_from_json(ids_json: dict) -> dict:
    """
    Generate an IDS XML file from an intermediate JSON specification.

    Args:
        ids_json: Dict matching the ``IdsDocument`` Pydantic schema.

    Returns:
        On success: ``{"success": True, "ids_xml": "...", "spec_count": N}``
        On Pydantic error: ``{"error": "...", "validation_errors": [...]}``
        On XSD error: ``{"error": "...", "xsd_errors": [...], "ids_xml": "..."}``
    """
    # Step 1: Pydantic validation
    try:
        doc = IdsDocument.model_validate(ids_json)
    except ValidationError as e:
        errors = []
        for err in e.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            errors.append(f"{loc}: {err['msg']}")
        logger.warning("IDS Pydantic validation failed: %s", errors)
        return {
            "error": "Invalid IDS JSON structure. Fix the errors and retry.",
            "validation_errors": errors,
        }

    # Step 2: Serialize to XML
    try:
        xml_string = serialize_ids(doc)
    except Exception as e:
        logger.error("IDS serialization failed: %s", e, exc_info=True)
        return {"error": f"XML serialization failed: {e}"}

    # Step 3: XSD validation
    is_valid, xsd_errors = validate_ids_xml(xml_string)

    if not is_valid:
        logger.warning("IDS XSD validation failed: %s", xsd_errors)
        return {
            "error": "Generated IDS XML failed XSD validation. Review the errors and fix the JSON.",
            "xsd_errors": xsd_errors,
            "ids_xml": xml_string,
        }

    logger.info(
        "IDS generated successfully: %d specification(s)", len(doc.specifications)
    )
    return {
        "success": True,
        "ids_xml": xml_string,
        "spec_count": len(doc.specifications),
    }
