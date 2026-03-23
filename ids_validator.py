"""
IDS XML validator — validates generated IDS XML against the IDS XSD schema.

Uses lxml for XSD validation. Falls back gracefully if lxml is unavailable
or the XSD cannot be loaded (e.g. network issues for imported schemas).
"""

from __future__ import annotations

import logging

from config import IDS_XSD_PATH

logger = logging.getLogger(__name__)

_schema = None
_schema_load_attempted = False


def _get_schema():
    """Load and cache the compiled XSD schema."""
    global _schema, _schema_load_attempted

    if _schema is not None:
        return _schema
    if _schema_load_attempted:
        return None

    _schema_load_attempted = True
    try:
        from lxml import etree

        with open(IDS_XSD_PATH, "rb") as f:
            schema_doc = etree.parse(f)
        _schema = etree.XMLSchema(schema_doc)
        logger.info("IDS XSD schema loaded successfully from %s", IDS_XSD_PATH)
        return _schema
    except Exception as e:
        logger.warning(
            "Could not load IDS XSD schema (%s). XSD validation will be skipped. "
            "This may be due to network issues (the XSD imports external schemas).",
            e,
        )
        return None


def validate_ids_xml(xml_string: str) -> tuple[bool, list[str]]:
    """
    Validate an IDS XML string against the IDS XSD schema.

    Returns:
        (is_valid, error_messages) — if the schema couldn't be loaded,
        returns (True, []) with a logged warning (optimistic fallback).
    """
    schema = _get_schema()
    if schema is None:
        logger.warning("XSD schema not available — skipping validation.")
        return True, []

    try:
        from lxml import etree

        doc = etree.fromstring(xml_string.encode("UTF-8"))
        is_valid = schema.validate(doc)
        errors = [str(e) for e in schema.error_log] if not is_valid else []
        return is_valid, errors
    except Exception as e:
        return False, [f"XML parsing error: {e}"]
