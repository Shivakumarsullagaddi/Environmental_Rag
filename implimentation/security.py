"""Security, Guardrails, Rate Limiting, and Session Ownership for Environmental AI Scientist."""

import os
import re
import time
import math
import uuid
import secrets
import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from fastapi import Request, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("environmental_security")

# ------------------------------------------------------------------------------
# Configuration & Constants
# ------------------------------------------------------------------------------
COOKIE_NAME = "env_visitor_id"
MAX_MESSAGE_LENGTH = int(os.getenv("MAX_MESSAGE_LENGTH", "8000"))
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", "65536"))  # 64 KB
RATE_LIMIT_GLOBAL_PER_MIN = int(os.getenv("RATE_LIMIT_GLOBAL_PER_MIN", "60"))
RATE_LIMIT_EXPENSIVE_PER_MIN = int(os.getenv("RATE_LIMIT_EXPENSIVE_PER_MIN", "15"))
MAX_TOOL_CALLS_PER_TURN = int(os.getenv("MAX_TOOL_CALLS_PER_TURN", "5"))

ALLOWED_CHART_TYPES = {"bar", "line", "pie", "doughnut", "radar"}

# Regex patterns for input validation and guardrails
RE_VISITOR_ID = re.compile(r"^[a-zA-Z0-9_\-]{16,64}$")
RE_SESSION_ID = re.compile(r"^[a-zA-Z0-9_\-]{8,128}$")

# Code execution patterns
CODE_EXEC_PATTERNS = [
    re.compile(r"\b(?:exec|eval)\s*\(", re.IGNORECASE),
    re.compile(r"\b(?:os\.system|subprocess\.|os\.popen|shutil\.)", re.IGNORECASE),
    re.compile(r"\b(?:__import__|importlib)\b", re.IGNORECASE),
    re.compile(r"\b(?:run|execute)\s+(?:this|the following)?\s*(?:python|code|script)\b", re.IGNORECASE),
    re.compile(r"\bexecute\s+it\s+\d+\s+times\b", re.IGNORECASE),
    re.compile(r"\brun\s+it\s+\d+\s+times\b", re.IGNORECASE),
    re.compile(r"\bdef\s+palindrome\b", re.IGNORECASE),
    re.compile(r"\bfork\s+bomb\b", re.IGNORECASE),
    re.compile(r"\bwhile\s+True\s*:", re.IGNORECASE),
]

# Computational DoS patterns
COMPUTATIONAL_DOS_PATTERNS = [
    re.compile(r"(?:e|10|2)\s*(?:\*\*|\^|raised to the power of)\s*(?:a\s+)?(?:million|\d{5,})", re.IGNORECASE),
    re.compile(r"(?:execute|run|repeat|loop)\s*(?:it\s*)?(?:for\s*)?(?:100[,\.]?000|\d{6,})\s*times", re.IGNORECASE),
]

# Prompt injection & exfiltration patterns
INJECTION_PATTERNS = [
    re.compile(r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous\s+)?(?:instructions|rules|system prompt)", re.IGNORECASE),
    re.compile(r"(?:reveal|print|show|dump)\s+(?:your\s+)?(?:system\s+prompt|instructions|secret|api[_-]key|\.env)", re.IGNORECASE),
    re.compile(r"(?:/etc/passwd|/proc/|/var/run/secrets)", re.IGNORECASE),
]

# General programming query patterns (unrelated to environmental science)
GENERAL_CODING_PATTERNS = [
    re.compile(r"\b(?:write|give me)\s+(?:a\s+)?(?:code|program|script|function)\s+(?:for|to find|to check)\s+(?:a\s+)?palindrome\b", re.IGNORECASE),
    re.compile(r"\b(?:write|implement)\s+(?:a\s+)?(?:quicksort|mergesort|binary search|bubble sort)\b", re.IGNORECASE),
    re.compile(r"\bleetcode\s*(?:problem|\d+)?\b", re.IGNORECASE),
]

# Key environmental science terms to confirm relevance
ENV_RELEVANCE_KEYWORDS = [
    "soil", "biodiversity", "ph", "carbon", "microb", "ecology", "ecosystem",
    "climate", "agriculture", "tillage", "crop", "water", "forest", "species",
    "nitrogen", "phosphorus", "fertilizer", "pesticide", "deforestation",
    "conservation", "pollution", "biomass", "flora", "fauna", "temperature",
    "rainfall", "habitat", "nutrient", "drainage", "tavily", "paper", "research",
    "book", "study", "evidence", "metric", "indicator", "environmental",
]


# ------------------------------------------------------------------------------
# 1. Visitor Session & Anonymous Ownership Management
# ------------------------------------------------------------------------------
class VisitorSessionManager:
    """Handles cryptographically secure anonymous visitor identification."""

    @staticmethod
    def generate_visitor_id() -> str:
        return "v_" + secrets.token_urlsafe(24)

    @staticmethod
    def get_visitor_id(request: Request) -> str:
        """Retrieves and validates the visitor token from HTTP headers or cookies."""
        # 1. Check X-Visitor-Id header (takes precedence for explicit API requests)
        header_val = request.headers.get("X-Visitor-Id")
        if header_val and RE_VISITOR_ID.match(header_val):
            return header_val

        # 2. Check HTTP-only cookie
        cookie_val = request.cookies.get(COOKIE_NAME)
        if cookie_val and RE_VISITOR_ID.match(cookie_val):
            return cookie_val

        # If not present or invalid, generate a fresh visitor token
        return VisitorSessionManager.generate_visitor_id()


class ConversationStore:
    """In-memory conversation store with strict owner-based isolation."""

    def __init__(self):
        # Maps session_id -> conversation dictionary
        self._conversations: Dict[str, Dict[str, Any]] = {}

    def create(self, session_id: str, owner_id: str, title: str = "New Investigation") -> Dict[str, Any]:
        """Creates and stores a conversation owned strictly by owner_id."""
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conv = {
            "session_id": session_id,
            "owner_id": owner_id,
            "title": title,
            "created_at": now_iso,
            "updated_at": now_iso,
            "messages": [],
        }
        self._conversations[session_id] = conv
        return conv

    def get(self, session_id: str, owner_id: str) -> Optional[Dict[str, Any]]:
        """Returns conversation if and only if it is owned by owner_id."""
        conv = self._conversations.get(session_id)
        if conv is None:
            return None
        if conv.get("owner_id") != owner_id:
            logger.warning(
                f"[SECURITY][UNAUTHORIZED_ACCESS] visitor={owner_id} attempted access to session={session_id} owned by {conv.get('owner_id')}"
            )
            return None
        return conv

    def list_for_owner(self, owner_id: str) -> List[Dict[str, Any]]:
        """Returns only the conversations owned by owner_id."""
        results = []
        for s_id, data in self._conversations.items():
            if data.get("owner_id") == owner_id:
                results.append({
                    "session_id": s_id,
                    "title": data.get("title", "Environmental Session"),
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                    "message_count": len(data.get("messages", [])),
                })
        results.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return results

    def delete(self, session_id: str, owner_id: str) -> bool:
        """Deletes conversation if owned by owner_id."""
        conv = self._conversations.get(session_id)
        if conv and conv.get("owner_id") == owner_id:
            del self._conversations[session_id]
            return True
        return False

    def contains(self, session_id: str) -> bool:
        return session_id in self._conversations

    def get_raw(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Internal lookup for server runtime state (requires internal authorization)."""
        return self._conversations.get(session_id)

    def total_count(self) -> int:
        return len(self._conversations)


# Singleton conversation store
conversation_store = ConversationStore()


# ------------------------------------------------------------------------------
# 2. Pre-Flight Security Guardian
# ------------------------------------------------------------------------------
class SecurityGuardian:
    """Pre-flight input inspector that blocks dangerous or out-of-domain requests before LLM/tools."""

    @staticmethod
    def inspect(query: str) -> Tuple[bool, Optional[str]]:
        """Inspects query text. Returns (is_safe, refusal_reason)."""
        if not query or not query.strip():
            return False, "Query cannot be empty."

        clean = query.strip()
        if len(clean) > MAX_MESSAGE_LENGTH:
            return False, f"Query exceeds maximum allowed length of {MAX_MESSAGE_LENGTH} characters."

        # 1. Check for explicit code execution requests
        for pat in CODE_EXEC_PATTERNS:
            if pat.search(clean):
                logger.warning(f"[SECURITY][CODE_EXEC_ATTEMPT] Blocked execution query: '{clean[:80]}'")
                return False, (
                    "Execution of arbitrary Python code or computational scripts is disabled "
                    "for server integrity and resource security. As an Environmental AI Scientist, "
                    "I provide evidence-grounded scientific reasoning based on peer-reviewed research "
                    "and textbook knowledge."
                )

        # 2. Check for Computational Denial of Service (DoS)
        for pat in COMPUTATIONAL_DOS_PATTERNS:
            if pat.search(clean):
                logger.warning(f"[SECURITY][COMPUTATIONAL_DOS] Blocked resource exhaustion: '{clean[:80]}'")
                return False, (
                    "High-volume computational or exponential arithmetic execution requests are "
                    "restricted to prevent resource exhaustion. Please submit an environmental "
                    "science or agricultural research query."
                )

        # 3. Check for prompt injection or system exfiltration attempts
        for pat in INJECTION_PATTERNS:
            if pat.search(clean):
                logger.warning(f"[SECURITY][PROMPT_INJECTION] Blocked injection attempt: '{clean[:80]}'")
                return False, (
                    "System prompt inspection, credential exfiltration, and directive overrides "
                    "are strictly restricted. System instructions and credentials remain protected."
                )

        # 4. Check for pure non-environmental coding homework
        for pat in GENERAL_CODING_PATTERNS:
            if pat.search(clean):
                # Verify if there is any environmental relevance
                lower = clean.lower()
                has_env = any(k in lower for k in ENV_RELEVANCE_KEYWORDS)
                if not has_env:
                    logger.info(f"[SECURITY][OUT_OF_DOMAIN] Refused generic coding request: '{clean[:80]}'")
                    return False, (
                        "I am an evidence-grounded Environmental AI Scientist. I cannot solve general "
                        "programming problems or write non-environmental algorithms (such as palindromes). "
                        "Please ask an environmental science, biodiversity, or agricultural ecology question."
                    )

        return True, None


# ------------------------------------------------------------------------------
# 3. Rate Limiter (Token Bucket / Sliding Window)
# ------------------------------------------------------------------------------
class RateLimiter:
    """In-memory sliding-window rate limiter per IP address with configurable limits."""

    def __init__(self):
        # Maps ip -> list of timestamps
        self._global_records: Dict[str, List[float]] = {}
        self._expensive_records: Dict[str, List[float]] = {}

    def is_allowed(self, ip: str, is_expensive: bool = False) -> Tuple[bool, int]:
        """Checks if a request from ip is permitted. Returns (allowed, retry_after_seconds)."""
        now = time.time()
        window = 60.0  # 1 minute

        # 1. Check Global Limit
        records = self._global_records.setdefault(ip, [])
        # Prune older than 60 seconds
        self._global_records[ip] = [t for t in records if now - t < window]
        if len(self._global_records[ip]) >= RATE_LIMIT_GLOBAL_PER_MIN:
            oldest = self._global_records[ip][0]
            retry_after = max(1, int(window - (now - oldest)))
            return False, retry_after

        # 2. Check Expensive Endpoint Limit (agent execution)
        if is_expensive:
            exp_records = self._expensive_records.setdefault(ip, [])
            self._expensive_records[ip] = [t for t in exp_records if now - t < window]
            if len(self._expensive_records[ip]) >= RATE_LIMIT_EXPENSIVE_PER_MIN:
                oldest = self._expensive_records[ip][0]
                retry_after = max(1, int(window - (now - oldest)))
                return False, retry_after

        # Record this request
        self._global_records[ip].append(now)
        if is_expensive:
            self._expensive_records[ip].append(now)

        return True, 0


# Singleton rate limiter
rate_limiter = RateLimiter()


# ------------------------------------------------------------------------------
# 4. Request Body Limit Middleware
# ------------------------------------------------------------------------------
class RequestBodyLimitMiddleware(BaseHTTPMiddleware):
    """Enforces maximum incoming request body size to protect against memory exhaustion."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        # Check Content-Length header if present
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    logger.warning(f"[SECURITY][BODY_LIMIT] Request exceeded Content-Length limit: {content_length} bytes")
                    return JSONResponse(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        content={"detail": f"Request body too large. Maximum allowed size is {self.max_bytes} bytes."},
                    )
            except ValueError:
                pass

        return await call_next(request)


# ------------------------------------------------------------------------------
# 5. Security Headers Middleware
# ------------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Applies strict HTTP security headers to all responses."""

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # Standard defense-in-depth headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # Restrictive Content Security Policy (allows self, CDN for Chart.js/Marked/DOMPurify, inline styles)
        csp_directives = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: https:",
            "connect-src 'self'",
            "frame-ancestors 'none'",
        ]
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)

        # Secure visitor cookie propagation if new visitor
        visitor_id = request.state.visitor_id if hasattr(request.state, "visitor_id") else None
        if visitor_id and COOKIE_NAME not in request.cookies:
            is_secure = request.url.scheme == "https"
            response.set_cookie(
                key=COOKIE_NAME,
                value=visitor_id,
                max_age=30 * 24 * 3600,  # 30 days
                httponly=True,
                samesite="lax",
                secure=is_secure,
                path="/",
            )

        return response


# ------------------------------------------------------------------------------
# 6. Structured Output & Chart Validator
# ------------------------------------------------------------------------------
class OutputSanitizer:
    """Validates and sanitizes assistant outputs, charts, and error messages."""

    @staticmethod
    def validate_chart(chart_obj: Any) -> Optional[Dict[str, Any]]:
        """Strictly validates a chart structure against injection and numerical anomalies."""
        if not isinstance(chart_obj, dict):
            return None

        chart_type = str(chart_obj.get("type", "bar")).lower()
        if chart_type not in ALLOWED_CHART_TYPES:
            chart_type = "bar"

        raw_labels = chart_obj.get("labels")
        raw_values = chart_obj.get("values")

        if not isinstance(raw_labels, list) or not isinstance(raw_values, list):
            return None
        if len(raw_labels) == 0 or len(raw_labels) != len(raw_values):
            return None
        if len(raw_labels) > 25:
            # Bound excessive labels
            raw_labels = raw_labels[:25]
            raw_values = raw_values[:25]

        clean_labels: List[str] = []
        clean_values: List[float] = []

        for lbl, val in zip(raw_labels, raw_values):
            # Sanitize label string
            lbl_str = str(lbl)[:80].replace("<", "&lt;").replace(">", "&gt;")
            try:
                val_float = float(val)
                # Reject NaN, Inf, or absurd numbers
                if math.isnan(val_float) or math.isinf(val_float) or abs(val_float) > 1e15:
                    return None
            except (ValueError, TypeError):
                return None

            clean_labels.append(lbl_str)
            clean_values.append(val_float)

        title = str(chart_obj.get("title", "Environmental Data Visualization"))[:120]
        title = title.replace("<", "&lt;").replace(">", "&gt;")

        unit = str(chart_obj.get("unit", ""))[:40].replace("<", "&lt;").replace(">", "&gt;")
        source = str(chart_obj.get("source", "Peer-Reviewed Scientific Literature"))[:150]
        source = source.replace("<", "&lt;").replace(">", "&gt;")

        return {
            "type": chart_type,
            "title": title,
            "labels": clean_labels,
            "values": clean_values,
            "unit": unit,
            "source": source,
        }

    @staticmethod
    def redact_sensitive(text: str) -> str:
        """Redacts accidental leaks of API keys, tokens, and internal paths."""
        if not text:
            return ""
        # Redact Google API keys
        text = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED_API_KEY]", text)
        # Redact Tavily keys
        text = re.sub(r"tvly-[a-zA-Z0-9]{20,}", "[REDACTED_TAVILY_KEY]", text)
        # Redact Bearer tokens
        text = re.sub(r"Bearer\s+[a-zA-Z0-9_\-\.]{20,}", "Bearer [REDACTED_TOKEN]", text)
        return text
