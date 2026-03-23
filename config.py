import os
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Paths
PROJECT_ROOT = Path(__file__).parent
IFC_SCHEMA_PATH = PROJECT_ROOT / "data" / "ifc-4.3.json"
EXPRESS_SCHEMA_PATH = PROJECT_ROOT / "data" / "IFC4X3_ADD2.exp.txt"
IDS_XSD_PATH = PROJECT_ROOT / "data" / "ids.xsd"

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
