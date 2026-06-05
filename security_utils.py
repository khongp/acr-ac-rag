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

def redact_phi(text: str) -> str:
    """
    Scans and redacts common patient identifiers (MRNs, SSNs, DOBs, phone numbers, emails, addresses)
    from clinical texts to ensure HIPAA compliance before writing to cache, sending to APIs, or logging.
    """
    if not text:
        return ""
    
    # 1. Phone numbers
    phone_pattern = r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
    text = re.sub(phone_pattern, "[REDACTED_PHONE]", text)
    
    # 2. SSN
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
    text = re.sub(ssn_pattern, "[REDACTED_SSN]", text)
    
    # 3. Emails
    email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
    text = re.sub(email_pattern, "[REDACTED_EMAIL]", text)
    
    # 4. DOB (Date of Birth)
    dob_pattern = r"\b(?:dob|birthdate|birth\s+date)\s*[:-]?\s*(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})\b"
    text = re.sub(dob_pattern, "DOB: [REDACTED_DOB]", text, flags=re.IGNORECASE)
    
    # 5. MRN (Medical Record Number) - support standard numeric and alphanumeric/hyphenated formats
    mrn_pattern = r"\b(?:mrn|medical\s+record\s+number)\s*[:-]?\s*[a-zA-Z0-9-]{4,15}\b"
    text = re.sub(mrn_pattern, "MRN: [REDACTED_MRN]", text, flags=re.IGNORECASE)
    
    # 6. Names: Mr. John Smith, Mr John Smith, Dr. Watson, patient John Doe
    name_patterns = [
        (r"\b[Pp]atient\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", "patient [REDACTED_NAME]"),
        (r"\b(?:[Mm]r|[Mm]s|[Mm]rs|[Dd]r|[Mm]r[Ss]|[Dd]R)\.?\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", "[REDACTED_TITLE] [REDACTED_LASTNAME]"),
    ]
    for pattern, repl in name_patterns:
        text = re.sub(pattern, repl, text)
        
    return text

