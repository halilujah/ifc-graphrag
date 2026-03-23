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

# Security
API_SECRET_KEY = os.getenv("API_SECRET_KEY", "")  # Required for /api/* in production
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")  # Comma-separated origins
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
MAX_CHAT_MESSAGE_LENGTH = int(os.getenv("MAX_CHAT_MESSAGE_LENGTH", "2000"))

# Server
PORT = int(os.getenv("PORT", "8080"))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
