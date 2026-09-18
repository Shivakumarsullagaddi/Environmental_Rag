"""Unified configuration for Environmental Scientist."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Search for .env in the same directory as config.py or parent directory
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path, override=False)
else:
    load_dotenv(override=False)

PROJECT_ID: str = os.getenv("GOOGLE_CLOUD_PROJECT", "developer-491706")
LOCATION: str = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
BQ_LOCATION: str = os.getenv("BIGQUERY_LOCATION", "US")
BQ_DATASET: str = os.getenv("BIGQUERY_DATASET", "developer-491706.darukaa_kb")
BQ_EMBEDDING_MODEL: str = os.getenv("BIGQUERY_EMBEDDING_MODEL", f"{BQ_DATASET}.embedding_model")

# Ensure Vertex AI authentication is active for Google GenAI / ADK
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "1"
os.environ["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
os.environ["GOOGLE_CLOUD_LOCATION"] = LOCATION

# Gemini Model
# Exactly gemini-3.5-flash
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# Tavily API Key
TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

def is_tavily_configured() -> bool:
    """Checks whether a valid Tavily API key is provided."""
    key = os.getenv("TAVILY_API_KEY", "").strip()
    return bool(key and key != "<VALUE WILL BE PROVIDED BY USER>" and not key.startswith("<"))

def get_masked_tavily_key() -> str:
    """Returns a masked representation of the Tavily API key for safe logging."""
    if not is_tavily_configured():
        return "[NOT_CONFIGURED]"
    key = TAVILY_API_KEY.strip()
    if len(key) <= 8:
        return "********"
    return f"{key[:4]}...{key[-4:]}"
