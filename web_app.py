"""
IFC Semantic Query Engine — Flask server with:
  - Interactive graph visualization (vis.js)
  - Chat interface powered by Gemini + Neo4j GraphRAG

Usage:
    python web_app.py
    # Open http://localhost:8080
"""

import json
import logging
import re
import time
from collections import defaultdict
from datetime import datetime
from functools import wraps

import neo4j
from flask import Flask, jsonify, render_template, request

from config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, PROJECT_ROOT,
    API_SECRET_KEY, UI_ACCESS_TOKEN, ALLOWED_ORIGINS, RATE_LIMIT_PER_MINUTE,
    MAX_CHAT_MESSAGE_LENGTH, PORT,
)
from main_orchestrator import run_agent

IDS_OUTPUT_DIR = PROJECT_ROOT / "data" / "ids_output"
IDS_OUTPUT_DIR.mkdir(exist_ok=True)

logger = logging.getLogger(__name__)

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Security middleware
# ---------------------------------------------------------------------------

# Simple in-memory rate limiter (per IP, sliding window)
_rate_log: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit() -> bool:
    """Return True if request is within rate limit."""
    ip = request.remote_addr or "unknown"
    now = time.time()
    window = _rate_log[ip]
    # Prune entries older than 60s
    _rate_log[ip] = [t for t in window if now - t < 60]
    if len(_rate_log[ip]) >= RATE_LIMIT_PER_MINUTE:
        return False
    _rate_log[ip].append(now)
    return True


@app.before_request
def _security_checks():
    """Run security checks before every API request."""
    # Only protect /api/* routes (except auth check)
    if not request.path.startswith("/api/") or request.path == "/api/auth/check":
        return None

    # 1. Access token check (link-based sharing)
    if UI_ACCESS_TOKEN:
        provided_token = request.headers.get("X-Access-Token", "")
        if provided_token != UI_ACCESS_TOKEN:
            return jsonify({"error": "Invalid or missing access token"}), 401

    # 2. API key check (skip if not configured — local dev)
    if API_SECRET_KEY:
        provided = request.headers.get("X-API-Key", "")
        if provided != API_SECRET_KEY:
            return jsonify({"error": "Invalid or missing API key"}), 401

    # 2. Rate limiting
    if not _check_rate_limit():
        return jsonify({"error": "Rate limit exceeded. Try again in a minute."}), 429


@app.after_request
def _add_cors_headers(response):
    """Add CORS headers to every response."""
    origin = request.headers.get("Origin", "")
    if ALLOWED_ORIGINS == "*":
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin and origin in [o.strip() for o in ALLOWED_ORIGINS.split(",")]:
        response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key, X-Access-Token"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response

_driver: neo4j.Driver | None = None


def _get_driver() -> neo4j.Driver:
    global _driver
    if _driver is None:
        _driver = neo4j.GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    return _driver


# ---------------------------------------------------------------------------
# HTML route
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html", api_key=API_SECRET_KEY)


# ---------------------------------------------------------------------------
# API: class search
# ---------------------------------------------------------------------------

@app.route("/api/auth/check", methods=["POST"])
def api_auth_check():
    """Check if the provided access token is valid. Called before security middleware runs."""
    data = request.get_json() or {}
    token = data.get("token", "")
    if not UI_ACCESS_TOKEN:
        return jsonify({"valid": True})
    return jsonify({"valid": token == UI_ACCESS_TOKEN})


@app.route("/api/classes")
def api_classes():
    """Return class list, optionally filtered by ?search=term."""
    search = request.args.get("search", "").strip()
    driver = _get_driver()

    if search:
        query = """
        MATCH (c:Class)
        WHERE toLower(c.name) CONTAINS toLower($term)
           OR toLower(c.code) CONTAINS toLower($term)
        RETURN c.code AS code, c.name AS name
        ORDER BY c.code
        LIMIT 50
        """
        params = {"term": search}
    else:
        query = """
        MATCH (c:Class)
        RETURN c.code AS code, c.name AS name
        ORDER BY c.code
        LIMIT 50
        """
        params = {}

    with driver.session() as session:
        records = list(session.run(query, **params))

    return jsonify([{"code": r["code"], "name": r["name"]} for r in records])


# ---------------------------------------------------------------------------
# API: single class graph (Class -> PropertySets -> Properties)
# ---------------------------------------------------------------------------

@app.route("/api/graph/<class_code>")
def api_graph_class(class_code: str):
    """Return vis.js nodes + edges for a single class and its full tree."""
    driver = _get_driver()

    query = """
    MATCH (c:Class {code: $code})
    OPTIONAL MATCH (c)-[:HAS_PROPERTY_SET]->(ps:PropertySet)-[:HAS_PROPERTY]->(p:Property)
    OPTIONAL MATCH (c)-[:INHERITS_FROM]->(parent:Class)
    RETURN c.code AS class_code,
           c.name AS class_name,
           c.definition AS class_def,
           parent.code AS parent_code,
           parent.name AS parent_name,
           ps.code AS pset_code,
           ps.name AS pset_name,
           ps.definition AS pset_def,
           p.code AS prop_code,
           p.name AS prop_name,
           p.definition AS prop_def,
           p.data_type AS prop_dtype,
           p.property_value_kind AS prop_kind,
           p.allowed_values AS prop_allowed
    """

    with driver.session() as session:
        records = list(session.run(query, code=class_code))

    if not records:
        return jsonify({"nodes": [], "edges": [], "error": f"Class '{class_code}' not found"}), 404

    nodes = {}
    edges = set()

    first = records[0]

    # Class node
    cid = f"class:{first['class_code']}"
    nodes[cid] = {
        "id": cid,
        "label": first["class_code"],
        "title": first["class_def"] or first["class_name"] or "",
        "group": "class",
        "meta": {"code": first["class_code"], "name": first["class_name"], "definition": first["class_def"]},
    }

    # Parent class node (if any)
    if first["parent_code"]:
        pid = f"class:{first['parent_code']}"
        nodes[pid] = {
            "id": pid,
            "label": first["parent_code"],
            "title": first["parent_name"] or "",
            "group": "parent",
            "meta": {"code": first["parent_code"], "name": first["parent_name"]},
        }
        edges.add((cid, pid, "INHERITS_FROM"))

    for rec in records:
        pset_code = rec["pset_code"]
        if pset_code is None:
            continue

        psid = f"pset:{pset_code}"
        if psid not in nodes:
            nodes[psid] = {
                "id": psid,
                "label": pset_code,
                "title": rec["pset_def"] or rec["pset_name"] or "",
                "group": "pset",
                "meta": {"code": pset_code, "name": rec["pset_name"], "definition": rec["pset_def"]},
            }
        edges.add((cid, psid, "HAS_PROPERTY_SET"))

        prop_code = rec["prop_code"]
        if prop_code is None:
            continue

        prid = f"prop:{pset_code}:{prop_code}"
        if prid not in nodes:
            # Parse allowed values for tooltip
            allowed_raw = rec["prop_allowed"]
            allowed = []
            if allowed_raw:
                try:
                    allowed = json.loads(allowed_raw)
                except (json.JSONDecodeError, TypeError):
                    pass

            tooltip = rec["prop_def"] or rec["prop_name"] or ""
            if rec["prop_dtype"]:
                tooltip += f"\nType: {rec['prop_dtype']}"
            if allowed:
                vals = ", ".join(a.get("Code", a.get("Value", "")) for a in allowed[:5])
                if len(allowed) > 5:
                    vals += f" ... (+{len(allowed)-5} more)"
                tooltip += f"\nAllowed: {vals}"

            nodes[prid] = {
                "id": prid,
                "label": prop_code,
                "title": tooltip,
                "group": "property",
                "meta": {
                    "code": prop_code,
                    "name": rec["prop_name"],
                    "definition": rec["prop_def"],
                    "data_type": rec["prop_dtype"],
                    "property_value_kind": rec["prop_kind"],
                    "allowed_values": allowed,
                },
            }
        edges.add((psid, prid, "HAS_PROPERTY"))

    # --- Structural layer: attributes + type targets ---
    attr_query = """
    MATCH (c:Class {code: $code})-[:HAS_ATTRIBUTE]->(a:Attribute)
    OPTIONAL MATCH (a)-[:ATTRIBUTE_TYPE]->(t)
    RETURN a.qualified_name AS attr_qname,
           a.name           AS attr_name,
           a.optional       AS attr_optional,
           a.raw_type       AS attr_raw_type,
           a.is_inverse     AS attr_is_inverse,
           a.position       AS attr_position,
           labels(t)[0]     AS type_label,
           CASE WHEN t:Class THEN t.code ELSE t.name END AS type_name
    ORDER BY a.is_inverse, a.position
    """

    with driver.session() as session:
        attr_records = list(session.run(attr_query, code=class_code))

    for rec in attr_records:
        attr_qname = rec["attr_qname"]
        if attr_qname is None:
            continue

        aid = f"attr:{attr_qname}"
        if aid not in nodes:
            tooltip = rec["attr_name"] or ""
            if rec["attr_raw_type"]:
                tooltip += f"\nType: {rec['attr_raw_type']}"
            if rec["attr_optional"]:
                tooltip += "\n(OPTIONAL)"
            if rec["attr_is_inverse"]:
                tooltip += "\n(INVERSE)"

            nodes[aid] = {
                "id": aid,
                "label": rec["attr_name"],
                "title": tooltip,
                "group": "attribute",
                "meta": {
                    "qualified_name": attr_qname,
                    "name": rec["attr_name"],
                    "optional": rec["attr_optional"],
                    "raw_type": rec["attr_raw_type"],
                    "is_inverse": rec["attr_is_inverse"],
                    "position": rec["attr_position"],
                },
            }
        edges.add((cid, aid, "HAS_ATTRIBUTE"))

        # Type target node
        type_name = rec["type_name"]
        type_label = rec["type_label"]
        if type_name and type_label:
            group_map = {"Class": "class", "Type": "type", "Enumeration": "enumeration", "SelectType": "selecttype"}
            tgroup = group_map.get(type_label, "type")
            tid = f"{tgroup}:{type_name}"
            if tid not in nodes:
                nodes[tid] = {
                    "id": tid,
                    "label": type_name,
                    "title": f"{type_label}: {type_name}",
                    "group": tgroup,
                    "meta": {"name": type_name, "label": type_label},
                }
            edges.add((aid, tid, "ATTRIBUTE_TYPE"))

    edge_list = [{"from": e[0], "to": e[1], "label": e[2], "arrows": "to"} for e in edges]

    return jsonify({
        "nodes": list(nodes.values()),
        "edges": edge_list,
    })


# ---------------------------------------------------------------------------
# API: overview graph (all classes -> their psets, no properties)
# ---------------------------------------------------------------------------

@app.route("/api/graph/overview")
def api_graph_overview():
    """Return a high-level graph of classes and their PropertySet connections."""
    limit = request.args.get("limit", 100, type=int)
    limit = min(limit, 500)

    driver = _get_driver()

    query = """
    MATCH (c:Class)-[:HAS_PROPERTY_SET]->(ps:PropertySet)
    WITH c, ps
    LIMIT $limit
    RETURN DISTINCT c.code AS class_code, c.name AS class_name,
           ps.code AS pset_code, ps.name AS pset_name
    """

    with driver.session() as session:
        records = list(session.run(query, limit=limit))

    nodes = {}
    edges = set()

    for rec in records:
        cid = f"class:{rec['class_code']}"
        if cid not in nodes:
            nodes[cid] = {
                "id": cid,
                "label": rec["class_code"],
                "title": rec["class_name"] or "",
                "group": "class",
            }

        psid = f"pset:{rec['pset_code']}"
        if psid not in nodes:
            nodes[psid] = {
                "id": psid,
                "label": rec["pset_code"],
                "title": rec["pset_name"] or "",
                "group": "pset",
            }

        edges.add((cid, psid))

    edge_list = [{"from": e[0], "to": e[1], "arrows": "to"} for e in edges]

    return jsonify({
        "nodes": list(nodes.values()),
        "edges": edge_list,
    })


# ---------------------------------------------------------------------------
# API: node detail
# ---------------------------------------------------------------------------

@app.route("/api/node/<node_type>/<code>")
def api_node_detail(node_type: str, code: str):
    """Return full details for a single node."""
    driver = _get_driver()

    label_map = {
        "class": "Class", "pset": "PropertySet", "property": "Property",
        "attribute": "Attribute", "type": "Type", "enumeration": "Enumeration",
        "selecttype": "SelectType", "enumvalue": "EnumValue",
    }
    label = label_map.get(node_type)
    if not label:
        return jsonify({"error": f"Unknown node type: {node_type}"}), 400

    # Different node types use different key fields
    key_map = {
        "Class": "code", "PropertySet": "code", "Property": "code",
        "Attribute": "qualified_name", "Type": "name", "Enumeration": "name",
        "SelectType": "name", "EnumValue": "qualified_name",
    }
    key_field = key_map.get(label, "code")
    query = f"MATCH (n:{label} {{{key_field}: $key}}) RETURN properties(n) AS props"

    with driver.session() as session:
        result = session.run(query, key=code).single()

    if not result:
        return jsonify({"error": f"{label} '{code}' not found"}), 404

    props = dict(result["props"])
    # Deserialize allowed_values if present
    if "allowed_values" in props and props["allowed_values"]:
        try:
            props["allowed_values"] = json.loads(props["allowed_values"])
        except (json.JSONDecodeError, TypeError):
            pass

    return jsonify(props)


# ---------------------------------------------------------------------------
# API: chat (Gemini-powered GraphRAG)
# ---------------------------------------------------------------------------

# In-memory conversation histories keyed by session_id
_chat_sessions: dict[str, list] = {}


@app.route("/api/chat", methods=["POST"])
def api_chat():
    """
    Chat with the IFC Semantic Query Engine.

    POST JSON:
        {
            "message": "What properties does IfcBridge need?",
            "session_id": "optional-session-id"
        }

    Returns:
        {
            "answer": "An IfcBridge requires ...",
            "tools_used": [{"tool": "query_class", "summary": "Queried IfcBridge"}],
            "graph_class": "IfcBridge" | null,
            "session_id": "abc123"
        }
    """
    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' field"}), 400

    user_message = data["message"].strip()
    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    if len(user_message) > MAX_CHAT_MESSAGE_LENGTH:
        return jsonify({"error": f"Message too long (max {MAX_CHAT_MESSAGE_LENGTH} chars)"}), 400

    session_id = data.get("session_id", "")
    if not session_id:
        import uuid
        session_id = str(uuid.uuid4())[:8]

    # Retrieve conversation history
    history = _chat_sessions.get(session_id)

    try:
        answer, updated_history, tool_log = run_agent(user_message, history)
    except Exception as e:
        logger.error("Chat agent error: %s", e, exc_info=True)
        return jsonify({"error": str(e), "session_id": session_id}), 500

    # Store updated history
    _chat_sessions[session_id] = updated_history

    # Detect if a specific class was queried (for graph update)
    graph_class = None
    ids_xml = None
    ids_filename = None
    for t in tool_log:
        if t["tool"] in ("query_class", "query_class_structure"):
            graph_class = graph_class or t["args"].get("class_code")
        if t.get("ids_xml"):
            ids_xml = t["ids_xml"]

    # Save IDS file to data/ids_output/
    if ids_xml:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ids_filename = f"ids_{timestamp}.ids"
        ids_path = IDS_OUTPUT_DIR / ids_filename
        ids_path.write_text(ids_xml, encoding="utf-8")
        logger.info("IDS file saved to %s", ids_path)

    # Strip large fields from tool_log before sending to frontend
    clean_log = []
    for t in tool_log:
        entry = {"tool": t["tool"], "summary": t["summary"]}
        clean_log.append(entry)

    return jsonify({
        "answer": answer,
        "tools_used": clean_log,
        "graph_class": graph_class,
        "session_id": session_id,
        "ids_xml": ids_xml,
        "ids_filename": ids_filename,
    })


@app.route("/api/chat/reset", methods=["POST"])
def api_chat_reset():
    """Reset a chat session."""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    if session_id in _chat_sessions:
        del _chat_sessions[session_id]
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting IFC Semantic Query Engine...")
    print(f"Open http://localhost:{PORT} in your browser")
    app.run(debug=True, host="0.0.0.0", port=PORT)
