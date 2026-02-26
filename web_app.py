"""
IFC Semantic Query Engine — Flask server with:
  - Interactive graph visualization (vis.js)
  - Chat interface powered by Gemini + Neo4j GraphRAG

Usage:
    python web_app.py
    # Open http://localhost:5000
"""

import json
import logging
import re

import neo4j
from flask import Flask, jsonify, render_template, request

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from main_orchestrator import run_agent

logger = logging.getLogger(__name__)

app = Flask(__name__)

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
    return render_template("index.html")


# ---------------------------------------------------------------------------
# API: class search
# ---------------------------------------------------------------------------

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

    label_map = {"class": "Class", "pset": "PropertySet", "property": "Property"}
    label = label_map.get(node_type)
    if not label:
        return jsonify({"error": f"Unknown node type: {node_type}"}), 400

    query = f"MATCH (n:{label} {{code: $code}}) RETURN properties(n) AS props"

    with driver.session() as session:
        result = session.run(query, code=code).single()

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
    for t in tool_log:
        if t["tool"] == "query_class":
            graph_class = t["args"].get("class_code")
            break

    return jsonify({
        "answer": answer,
        "tools_used": tool_log,
        "graph_class": graph_class,
        "session_id": session_id,
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
    print("Open http://localhost:5000 in your browser")
    app.run(debug=True, port=5000)
