import re

# Prompt-injection patterns to filter threat inputs from clinical texts
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|all|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(your|all|the)\s+(system|previous|prior)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"new\s+instruction", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|your\s+instructions)", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a\s+|an\s+)?(different|new|unrestricted)", re.IGNORECASE),
    re.compile(r"<\s*(script|iframe|object|embed)", re.IGNORECASE),
]
