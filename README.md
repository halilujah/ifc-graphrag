# IFC 4.3 Semantic Query Engine

A GraphRAG-powered web application that answers natural language questions about the **IFC 4.3 standard** using a Neo4j knowledge graph and Google Gemini.

Ask a question in plain English, get a structured answer grounded in real graph data, and see the relevant knowledge graph rendered live.

![Architecture](https://img.shields.io/badge/GraphRAG-Neo4j%20%2B%20Gemini-blue)

## How It Works

```
User: "What properties does IfcBridge need?"
       |
       v
  Gemini 2.5 Flash (LLM with function calling)
       |
       +---> query_class("IfcBridge")      --> Neo4j lookup
       +---> search_classes("bridge")      --> keyword search
       +---> run_cypher("MATCH ...")       --> read-only Cypher
       |
       v
  Structured answer + live graph visualization
```

The LLM doesn't guess — it retrieves facts from the knowledge graph using 3 tools, then synthesizes a grounded answer. This is **GraphRAG**: Retrieval-Augmented Generation backed by a graph database instead of vector search.

## Knowledge Graph

Built from the official IFC 4.3 JSON schema:

- **1,418** Class nodes (IfcWall, IfcBridge, IfcDoor, ...)
- **746** PropertySet nodes (Pset_WallCommon, Pset_BridgeCommon, ...)
- **2,501** Property nodes (LoadBearing, FireRating, ...)
- Relationships: `HAS_PROPERTY_SET`, `HAS_PROPERTY`, `INHERITS_FROM`

## Features

- **Chat interface** — ask questions in natural language
- **Live graph visualization** — vis.js force-directed graph updates with each answer
- **3 LLM tools** — class lookup, keyword search, and raw Cypher queries
- **Multi-turn conversations** — the system remembers context across messages
- **Node details panel** — click any node to see its full definition and metadata
- **Class search** — autocomplete search bar for browsing IFC classes

## Setup

### Prerequisites

- Python 3.10+
- Docker (for Neo4j)
- Gemini API key (free tier: [aistudio.google.com/apikey](https://aistudio.google.com/apikey))

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Start Neo4j

```bash
docker run -d --name neo4j \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/password123 \
  neo4j:5
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your Neo4j password and Gemini API key
```

### 4. Ingest the IFC schema

Place `ifc-4.3.json` in the `data/` directory, then:

```bash
python ingest_graph.py
```

This parses the IFC 4.3 JSON and loads all classes, property sets, and properties into Neo4j.

### 5. Run the web app

```bash
python web_app.py
```

Open [http://localhost:5000](http://localhost:5000)

## Example Prompts

**Class lookups:**
- What properties does IfcBridge need?
- Tell me about IfcActuator and its property sets

**Graph analytics (Cypher):**
- Which IFC classes have the most property sets?
- Which property sets are shared by the most classes?
- What is the inheritance chain of IfcBridge?

**Comparisons:**
- Which property sets are common between IfcWall and IfcBeam?
- Compare the properties of IfcDoor and IfcWindow

**Multi-turn:**
- "Tell me about IfcWall" → "What about its parent class?" → "Which other classes share the same parent?"

## Project Structure

```
ifc-ai/
  config.py              # Environment config (Neo4j, Gemini)
  ingest_graph.py        # Parses ifc-4.3.json -> Neo4j
  neuro_agent.py         # Neo4j query layer
  main_orchestrator.py   # Gemini agent with tool calling
  web_app.py             # Flask server + REST API
  templates/
    index.html           # Chat + graph visualization UI
  data/
    ifc-4.3.json         # IFC 4.3 schema (not in repo)
```

## Tech Stack

- **LLM**: Google Gemini 2.5 Flash (free tier)
- **Graph DB**: Neo4j 5
- **Backend**: Flask (Python)
- **Frontend**: vis.js (graph), vanilla JS (chat)
- **Data**: IFC 4.3 official schema (buildingSMART)
