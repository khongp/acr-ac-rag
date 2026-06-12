# ACR-AC-RAG — Comprehensive Codebase Review

> **Scope**: Full review of all major source files (~300 KB+ of code across 16 files)
> **Date**: June 11, 2026
> **Method**: Direct file-by-file analysis with exact line references

---

## Executive Summary

| Severity | Count | Key Areas |
|----------|-------|-----------|
| 🔴 Critical | 3 | API key in `.env`, XSS via copilot chat, unsafe pickle deserialization |
| 🟠 High | 7 | No auth on sensitive endpoints, error info leakage, copilot memory leak, conn mgmt |
| 🟡 Medium | 12 | Prompt injection gaps, LIKE wildcards, confidence-score semantics, missing validation |
| 🔵 Low / Code Quality | 10+ | Print statements, magic numbers, unused modules, duplicate entry points |
| ⚡ Performance | 5 | BM25 rebuild, embedding cache misses, regex recompilation, frontend size |

> [!IMPORTANT]
> This project already demonstrates many **good security practices**: PHI redaction, prompt injection pattern detection, parameterized SQL throughout `protocol_db.py` and `rag_engine.py`, rate limiting via `slowapi`, non-root Docker user, health checks, WAL mode for SQLite, and CORS from env vars. The issues below are areas for further hardening.

---

## 🔴 Critical Issues (Fix Immediately)

### C1. API Key Exposed in `.env` File

**File**: [.env](file:///G:/My Drive/ACR-AC-RAG/.env) — Line 1

```
GOOGLE_API_KEY="AIzaSyDbfyu-doWG3tIGuaWognyPrPXkfUJPq8c"
```

The `.env` file contains a live Google API key. While `.gitignore` does exclude `.env` and `git log --all -- .env` shows it was never committed, this file sits on Google Drive which syncs and retains file history.

**Risks**:
- Google Drive file history preserves all versions indefinitely
- Anyone with shared drive access can read this key
- If this repo is ever open-sourced, an oversight could leak the key

**Recommended fix**:
- Rotate this API key immediately as a precaution
- Use Google Cloud Secret Manager or a `.env.local` file not on shared drives
- Consider using Application Default Credentials (ADC) instead of an explicit key for Cloud Run

---

### C2. XSS via Copilot Chat — `innerHTML` with Parsed Markdown

**File**: [index.html](file:///G:/My Drive/ACR-AC-RAG/index.html) — Line 3767

```javascript
bubble.innerHTML = parseMarkdown(content);
```

The `parseMarkdown()` function ([line 2951](file:///G:/My Drive/ACR-AC-RAG/index.html#L2951-L2961)) **does** escape `<`, `>`, and `&` — which is good. However, the function then re-introduces HTML tags via regex replacements (bold → `<strong>`, headers → `<h1>`, etc.). If the LLM response contains markdown-like patterns crafted by a prompt injection attacker, the escaping-then-unescaping pipeline can be bypassed.

**Specific attack vector**:
The copilot chat renders LLM output that includes user-supplied conversation history ([line 3767](file:///G:/My Drive/ACR-AC-RAG/index.html#L3767)). An attacker could craft input that, after markdown parsing, produces valid HTML with event handlers. For example, markdown bold `**` wrapping around crafted content could construct tags.

**More critical**: Multiple other `innerHTML` uses insert data directly from API responses without going through `parseMarkdown` at all:
- [Line 3135](file:///G:/My Drive/ACR-AC-RAG/index.html#L3135): `finalBox.innerHTML` renders `parseMarkdown(pea.final)` — passes through markdown parser ✅
- [Line 3172](file:///G:/My Drive/ACR-AC-RAG/index.html#L3172): `detailsContent.innerHTML = detailsHtml.join('')` — renders document content from API sources without sanitization ⚠️
- [Line 3258](file:///G:/My Drive/ACR-AC-RAG/index.html#L3258): `container.innerHTML = rowsHtml.join('')` — renders procedure table rows built from API data ⚠️

**Recommended fix**:
- Add [DOMPurify](https://github.com/cure53/DOMPurify) as a final sanitization step before any `innerHTML` assignment
- Or switch to `textContent` for plain-text fields and a proper markdown library (e.g., `marked` with `sanitize: true`) for rich content

---

### C3. Unsafe `pickle.load` — Arbitrary Code Execution Risk

**File**: [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py) — Lines 514-515, 527-528

```python
with open(bm25_path, "rb") as f:
    retriever = pickle.load(f)  # Line 515

with open(chunks_path, "rb") as f:
    chunks = pickle.load(f)     # Line 528
```

`pickle.load()` deserializes arbitrary Python objects and can execute arbitrary code. If the `.pkl` files on the GCS mount are tampered with (supply chain attack, compromised GCS bucket), an attacker gains full remote code execution inside the container.

**Recommended fix**:
- Replace pickle with a safer serialization format (JSON for chunks, or `safetensors`/`msgpack`)
- If pickle must be used, add HMAC integrity verification before loading:
```python
import hmac
expected_mac = read_mac_file(bm25_path + ".mac")
with open(bm25_path, "rb") as f:
    data = f.read()
    if not hmac.compare_digest(hmac.new(secret, data, 'sha256').digest(), expected_mac):
        raise SecurityError("BM25 file integrity check failed")
    retriever = pickle.loads(data)
```

---

## 🟠 High Severity Issues

### H1. No Authentication on Sensitive Endpoints

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py)

The following endpoints modify state or expose sensitive data with **no authentication**:

| Endpoint | Line | Risk |
|----------|------|------|
| `POST /v1/override` | [553](file:///G:/My Drive/ACR-AC-RAG/main.py#L553) | Anyone can write fake clinician overrides to the audit DB |
| `POST /v1/accept-mapping` | [571](file:///G:/My Drive/ACR-AC-RAG/main.py#L571) | Self-learning writeback — can poison the protocol mapping |
| `POST /v1/review/claim` | [647](file:///G:/My Drive/ACR-AC-RAG/main.py#L647) | Attacker can claim all review queue cases |
| `POST /v1/review/resolve` | [686](file:///G:/My Drive/ACR-AC-RAG/main.py#L686) | Attacker can resolve cases with fake recommendations |
| `GET /v1/overrides` | [594](file:///G:/My Drive/ACR-AC-RAG/main.py#L594) | Exposes clinical override audit trail |
| `GET /v1/review/queue` | [606](file:///G:/My Drive/ACR-AC-RAG/main.py#L606) | Exposes clinical scenario text from patients |

The read endpoints (`/v1/analyze`, `/v1/protocol`) are rate-limited (30/min) which provides some protection, but the write endpoints have no auth at all.

**Recommended fix**: Add at minimum an API key middleware for write endpoints:
```python
from fastapi import Depends, Header

async def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != os.getenv("ADMIN_API_KEY"):
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.post("/v1/override", dependencies=[Depends(verify_api_key)])
```

---

### H2. Error Messages Leak Internal Details

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) — Lines 366, 377, 457, 471, 496, 568, 590

Multiple endpoints expose raw exception messages to clients:
```python
raise HTTPException(status_code=500, detail=f"Error in Text-to-FHIR conversion: {str(e)}")  # Line 366
raise HTTPException(status_code=500, detail=f"Error in RAG retrieval: {str(e)}")              # Line 377
```

These can reveal:
- Internal file paths and database locations
- API key validation errors
- Stack trace fragments with module names

**Recommended fix**: Return generic errors externally, log details internally:
```python
except Exception as e:
    logger.error("Error in RAG retrieval", exc_info=True)
    raise HTTPException(status_code=500, detail="Error processing clinical scenario. Please try again.")
```

---

### H3. Copilot RAG Cache — Unbounded Memory Growth

**File**: [copilot_engine.py](file:///G:/My Drive/ACR-AC-RAG/copilot_engine.py) — Lines 16, 96-101

```python
_rag_cache = {}  # Line 16 — unbounded dict

# Line 96-101
cache_key = req.scenario_text.strip().lower()
if cache_key in _rag_cache:
    acr_result = _rag_cache[cache_key]
else:
    acr_result = query_acr_guidelines(req.scenario_text)
    _rag_cache[cache_key] = acr_result  # Never evicted!
```

Each unique scenario text adds an entry that is **never removed**. On a long-running server, this will cause unbounded memory growth. Each cached RAG result contains full source documents (~10-50 KB each).

**Recommended fix**:
```python
from functools import lru_cache
# Or use a TTL cache:
from cachetools import TTLCache
_rag_cache = TTLCache(maxsize=200, ttl=3600)  # 200 entries, 1 hour TTL
```

---

### H4. SQLite Connection Not Closed on Exception Paths

**File**: [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py) — Lines 131-139, 207-215

Several functions open connections and close them in the happy path, but if an exception occurs between `get_db_connection()` and `conn.close()`, the connection leaks:

```python
def add_clinician_override(...):
    # ...
    conn = get_db_connection(CACHE_DB_PATH)     # Line 131
    cursor = conn.cursor()                       # Line 132
    # ... operations ...
    conn.commit()                                # Line 138
    conn.close()                                 # Line 139 — skipped if Line 137 throws!
```

**Contrast** with [protocol_db.py](file:///G:/My Drive/ACR-AC-RAG/protocol_db.py#L30-L49) which uses `@contextmanager` with `try/finally` — this is the correct pattern.

**Recommended fix**: Use context managers consistently:
```python
def add_clinician_override(...):
    with contextlib.closing(get_db_connection(CACHE_DB_PATH)) as conn:
        cursor = conn.cursor()
        # ...
        conn.commit()
```

---

### H5. `rag_initialized` Flag Has a Race Window

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) — Lines 112-125

```python
async def ensure_rag_ready():
    global rag_initialized, rag_error
    if not rag_initialized:                         # Line 114 — read
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, init_rag)
        rag_initialized = True                      # Line 118 — write
```

With multiple concurrent requests hitting `ensure_rag_ready()` before startup completes, `init_rag()` could be called multiple times simultaneously. While `init_rag()` has internal singleton checks, the GCS file copy operations within it ([lines 1018-1078](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L1018-L1078)) are not fully atomic despite the `os.rename` pattern — the `except (FileExistsError, OSError)` catch is correct but still represents a race window.

**Recommended fix**: Use `asyncio.Lock`:
```python
_init_lock = asyncio.Lock()

async def ensure_rag_ready():
    global rag_initialized
    if rag_initialized:
        return
    async with _init_lock:
        if not rag_initialized:
            await loop.run_in_executor(None, init_rag)
            rag_initialized = True
```

---

### H6. `health` Endpoint Exposes Internal Error Details

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) — Lines 247-252

```python
if rag_error:
    return {
        "status": "degraded",
        "error": rag_error,  # Contains str(exception) — raw internal error
        "message": "RAG engine failed to initialize on startup..."
    }
```

The health endpoint returns `rag_error` which is `str(e)` from startup ([line 145](file:///G:/My Drive/ACR-AC-RAG/main.py#L145)). This is unauthenticated and can leak API key errors, file paths, etc.

**Recommended fix**: Only return the `message`, not the raw error:
```python
return {"status": "degraded", "message": "RAG engine failed to initialize. Check server logs."}
```

---

### H7. CDS Hook Error Card Leaks Exception Details

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) — Line 848

```python
"detail": f"Failed to retrieve ACR appropriateness criteria: {str(e)}",
```

CDS Hook error cards sent to EHR systems should not contain internal exception messages.

---

## 🟡 Medium Severity Issues

### M1. Prompt Injection Pattern Detection Is Incomplete

**File**: [security_utils.py](file:///G:/My Drive/ACR-AC-RAG/security_utils.py) — Lines 4-12

The project has a good foundation with 7 injection patterns, but they can be bypassed through:
- **Unicode homoglyphs**: `іgnore prevіous іnstructіons` (Cyrillic і)
- **Character insertion**: `i.g.n.o.r.e previous instructions`
- **Encoding tricks**: `ignore%20previous%20instructions`
- **Indirect injection via ingested documents**: Documents in ChromaDB are not scanned for injection patterns before they become RAG context

**Note**: The copilot engine ([copilot_engine.py:45-52](file:///G:/My Drive/ACR-AC-RAG/copilot_engine.py#L45-L52)) correctly rejects matching inputs, while the RAG engine ([rag_engine.py:1244-1248](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L1244-L1248)) strips patterns but continues processing. The copilot approach (reject) is safer for a medical application.

---

### M2. `LIKE` Wildcards in Protocol Lookup

**File**: [protocol_db.py](file:///G:/My Drive/ACR-AC-RAG/protocol_db.py) — Lines 305-308

```python
query = "... WHERE ... apm.acr_procedure_text LIKE ?"
params = [institution_id, f"%{acr_procedure_text}%"]
```

While this uses parameterized queries (✅ no SQL injection), the `%wildcard%` pattern has functional issues:
- A search for `"CT"` would match `"CT Abdomen"`, `"CT Chest"`, but also `"eCT"`, `"director"`, etc.
- In `search_protocols_fulltext` ([lines 446-467](file:///G:/My Drive/ACR-AC-RAG/protocol_db.py#L446-L467)) the same pattern is used for full-text search

**Recommended fix**: Use SQLite FTS5 for proper full-text search, or at minimum add word-boundary awareness.

---

### M3. Confidence Score Semantics Are Inverted

**File**: [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py) — Lines 1310-1312

```python
scores = [doc.metadata.get("score", 1.0) for doc in docs[:3]]
confidence = sum(scores) / len(scores) if scores else 1.0
```

ChromaDB `similarity_search_with_score` returns **distance** (lower = better), not **similarity** (higher = better). A score of 0.1 means very similar, but this code treats it as low confidence (0.1 avg → below `AMBIGUITY_THRESHOLD = 0.55`), which would incorrectly route good matches to manual review.

**Impact**: This may be mitigated by the fact that the hybrid retrieval pipeline assigns `score: 1.0` as default ([lines 658, 672, 739](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L658)), so most docs have score=1.0. But when actual similarity scores are populated, the semantics are wrong.

**Recommended fix**: Normalize distance to similarity: `confidence = 1.0 / (1.0 + avg_distance)`

---

### M4. Shared `_llm_fast` Global Variable

**File**: [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py) — Lines 561-564, 1139-1142

```python
global _llm_fast
if _llm_fast is None:
    from llm_router import get_llm_fast
    _llm_fast = get_llm_fast()
```

This lazy initialization pattern appears in multiple functions without synchronization. If two threads hit `_rerank_scenarios_llm` and `_expand_clinical_query` simultaneously before `_llm_fast` is initialized, both may call `get_llm_fast()` concurrently. While `get_llm` uses `@lru_cache` ([llm_router.py:53](file:///G:/My Drive/ACR-AC-RAG/llm_router.py#L53)), `lru_cache` is not thread-safe in Python < 3.12.

---

### M5. Missing Input Validation on Copilot Chat History

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) — Lines 748-757

```python
for m in req.chat_history:
    history.append(ChatMessage(role=m.get("role", "user"), content=m.get("content", "")))
```

Chat history messages have no:
- Maximum count limit (attacker could send 1000 history messages)
- Maximum content length per message
- Role validation (only "user" and "model"/"assistant" should be allowed)

This could be used to:
- Exhaust the LLM context window
- Inject system-role messages into the conversation

**Recommended fix**: Add validation to `CoPilotChatRequest`:
```python
class CoPilotChatRequest(BaseModel):
    scenario_text: str = Field(max_length=4000)
    chat_history: list = Field(default_factory=list, max_length=20)  # Max 20 turns
    message: str = Field(max_length=4000)
```

---

### M6. No `max_length` on Several Request Fields

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py)

While `AnalyzeRequest.text` and `ProtocolRequest.text` have `max_length=4000` ✅, these models have no limits:

```python
class OverrideRequest(BaseModel):           # Line 215
    query_key: str                           # No max_length
    original_recommendation: str             # No max_length
    overridden_recommendation: str           # No max_length
    override_reason: str                     # No max_length
    clinician_notes: str = ""                # No max_length

class ResolveReviewRequest(BaseModel):       # Line 236
    final_recommendation: str                # No max_length — written to SQLite
```

An attacker could submit megabytes of text in these fields.

---

### M7. `parseMarkdown` Regex ReDoS Risk

**File**: [index.html](file:///G:/My Drive/ACR-AC-RAG/index.html) — Line 2984

```javascript
html = html.replace(/((?:<li[^>]*>.*?<\/li>\s*)+)/g, '...');
```

This nested quantifier with backtracking could cause catastrophic backtracking on crafted input (ReDoS). Since this runs in the browser, it would freeze the UI.

---

### M8. `embed_query` Has No Retry Logic

**File**: [ingest.py](file:///G:/My Drive/ACR-AC-RAG/ingest.py) — Lines 172-183

```python
def embed_query(self, text: str) -> List[float]:
    cached = self._get_cached_embedding(text)
    if cached is not None:
        return cached
    # No retry on API failure!
    response = self.client.models.embed_content(...)
```

`embed_documents` has robust retry logic with exponential backoff ([lines 130-154](file:///G:/My Drive/ACR-AC-RAG/ingest.py#L130-L154)), but `embed_query` (called on every user query) has **none**. A transient API error would crash the request.

---

### M9. FHIR Bundle Validation Skipped

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) — Lines 367-368

```python
else:
    bundle_dict = req.bundle  # No validation at all!
```

When a raw FHIR bundle is provided via the `bundle` field, it's used directly without any validation against the FHIR spec. Malformed bundles could cause crashes in downstream processing.

---

### M10. Audit Token Hash Truncation

**File**: [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) — Lines 45-48

```python
content_hash = hashlib.sha256(content.encode()).hexdigest()[:12].upper()
```

Truncating SHA-256 to 12 hex characters (48 bits) gives ~2^24 (16 million) entries before a birthday collision is likely. For audit tokens, this is probably fine in practice but worth noting.

---

### M11. Missing Backup Directory in `.gitignore`

**File**: [.gitignore](file:///G:/My Drive/ACR-AC-RAG/.gitignore)

The backup directories `chroma_db_backup/`, `chroma_db_gemini_backup/`, and `chroma_db_local_backup/` exist in the project root but are not in `.gitignore`. These directories could accidentally be committed.

---

### M12. Thread Safety of `CachedGoogleGenerativeAIEmbeddings` Cache Writes

**File**: [ingest.py](file:///G:/My Drive/ACR-AC-RAG/ingest.py) — Lines 98-108

```python
def _set_cached_embedding(self, text: str, embedding: List[float]):
    cursor = self.conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO embeddings ...", (...))
    self.conn.commit()  # Each write commits individually
```

While thread-local connections ([line 57](file:///G:/My Drive/ACR-AC-RAG/ingest.py#L57)) prevent cross-thread issues, the per-embedding commit pattern in `embed_documents` means N commits for N embeddings in a batch. Wrapping the entire batch in a single transaction would be 10-100x faster.

---

## ⚡ Performance Optimizations

### P1. Sequential Per-Embedding Cache Commits

**File**: [ingest.py](file:///G:/My Drive/ACR-AC-RAG/ingest.py) — Lines 160-162

Each embedding is cached with an individual `INSERT + COMMIT`. For a batch of 100, that's 100 commits.

**Fix**: Batch the cache writes:
```python
# After processing a batch:
for idx, text, emb in zip(batch_indices, batch, batch_embs):
    cursor.execute("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?)",
                   (self._get_hash(text), text, json.dumps(emb)))
self.conn.commit()  # Single commit for entire batch
```

---

### P2. `_rag_cache` in Copilot Uses Raw String Key

**File**: [copilot_engine.py](file:///G:/My Drive/ACR-AC-RAG/copilot_engine.py) — Line 95

```python
cache_key = req.scenario_text.strip().lower()
```

This doesn't apply PHI redaction before caching, so `"70yo John Smith with headache"` and `"70yo Jane Doe with headache"` are different cache entries even though they'd redact to the same key. The RAG engine's cache uses `redact_phi()` ([rag_engine.py:177](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L177)), creating a consistency gap.

---

### P3. `print()` Statements Instead of `logging`

**Files**: [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py), [copilot_engine.py](file:///G:/My Drive/ACR-AC-RAG/copilot_engine.py), [safety_engine.py](file:///G:/My Drive/ACR-AC-RAG/safety_engine.py), [ingest.py](file:///G:/My Drive/ACR-AC-RAG/ingest.py), [llm_router.py](file:///G:/My Drive/ACR-AC-RAG/llm_router.py)

While [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py) correctly uses `logging` ✅, all other modules use `print()`. This means:
- No log levels — can't silence debug output in production
- No timestamps on engine messages
- No correlation with request IDs from `main.py`'s middleware

---

### P4. Frontend Size — 197 KB Single HTML File

**File**: [index.html](file:///G:/My Drive/ACR-AC-RAG/index.html) — 4,423 lines

The entire frontend (HTML + CSS + JS) is in one file. This prevents:
- Browser caching of CSS/JS independently
- Code splitting / lazy loading
- Minification and compression

The `logo.png` (1.6 MB) is also loaded eagerly without `loading="lazy"`.

---

### P5. Missing `__all__` Exports in Engine Modules

Files like `rag_engine.py`, `safety_engine.py`, and `protocol_db.py` export many functions but don't define `__all__`. This means `from module import *` pulls in everything, including internal helpers prefixed with `_`.

---

## 🔵 Low / Code Quality Issues

### L1. Duplicate Entry Points

[app.py](file:///G:/My Drive/ACR-AC-RAG/app.py) is a legacy Streamlit frontend pointing at `localhost:8000` ([line 10](file:///G:/My Drive/ACR-AC-RAG/app.py#L10)) while the current API runs on `8080`. It also uses the old `/v1/analyze` endpoint format. This file appears unused and should be removed or archived.

### L2. Magic Numbers in RAG Engine

**File**: [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py)

| Value | Line | Meaning |
|-------|------|---------|
| `k=60` | [457](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L457) | RRF k parameter |
| `k_tables=30` | [634](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L634) | Vector table retrieval count |
| `k_narrative=3` | [635](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L635) | Narrative chunk count |
| `top_k=15` | [768](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L768) | ColBERT rerank top-k |
| `> 7` days | [189](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L189) | Cache TTL |
| `4000` | [1249](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L1249) | Max input length |

These should be in a config module or environment variables.

### L3. MD5 for Guideline Versioning

**File**: [ingest.py](file:///G:/My Drive/ACR-AC-RAG/ingest.py) — Lines 187-192

```python
def get_guideline_version(source_path: str) -> str:
    h = hashlib.md5()
```

MD5 is used for content hashing. While not a security concern here (it's for version tracking, not deduplication), SHA-256 is preferred as a matter of practice.

### L4. Unused/Stale Files in Project Root

Several files in the root directory appear to be development artifacts:
- `out.txt` (2.2 KB), `safety_out.log` (3.1 KB), `test_out.log` (4.8 KB), `uvicorn_output.log` (3.8 KB)
- `tables_sample.json`, `topic_tables.json`
- `=` (0 bytes — likely an accidental file creation)
- Multiple `check_*.py`, `diagnose_*.py`, `dump_*.py` scripts

### L5. `check_same_thread=False` on SQLite Connections

**File**: [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py) — Line 78

```python
conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
```

This disables SQLite's thread safety check. While WAL mode provides some concurrency support, SQLite connections are fundamentally not thread-safe. This is compensated by short-lived connections and the fact that FastAPI runs queries in a thread pool via `run_in_executor`.

---

## ✅ Things Done Well

Credit where it's due — the codebase demonstrates strong engineering in several areas:

| Feature | Location | Assessment |
|---------|----------|------------|
| **PHI Redaction** | [security_utils.py](file:///G:/My Drive/ACR-AC-RAG/security_utils.py) | Good coverage of SSN, MRN, phone, email, DOB, names |
| **Parameterized SQL** | [protocol_db.py](file:///G:/My Drive/ACR-AC-RAG/protocol_db.py), [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py) | All queries use `?` placeholders — no SQL injection |
| **Rate Limiting** | [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py#L152) | `slowapi` with 30/min on query endpoints |
| **Non-root Docker** | [Dockerfile](file:///G:/My Drive/ACR-AC-RAG/Dockerfile#L28-L29) | `adduser` + `USER appuser` ✅ |
| **Health Check** | [Dockerfile](file:///G:/My Drive/ACR-AC-RAG/Dockerfile#L32-L33), [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py#L243) | Both Docker HEALTHCHECK and `/health` endpoint |
| **CORS from env** | [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py#L163-L189) | Not hardcoded `*` — reads from `ALLOWED_ORIGINS` |
| **Audit Trail** | [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py#L60-L84) | JSONL audit log with tamper-evident tokens |
| **Abstention Gate** | [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L1161-L1233) | Refuses to answer with insufficient clinical detail |
| **WAL Mode** | [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L80) | SQLite WAL for concurrent access |
| **Async execution** | [main.py](file:///G:/My Drive/ACR-AC-RAG/main.py#L374) | `asyncio.to_thread()` for blocking calls ✅ |
| **Retry with backoff** | [rag_engine.py](file:///G:/My Drive/ACR-AC-RAG/rag_engine.py#L1127-L1129) | `tenacity` retry on LLM calls |
| **Embedding cache** | [ingest.py](file:///G:/My Drive/ACR-AC-RAG/ingest.py#L43-L183) | SQLite cache prevents redundant API calls |
| **Pinned deps** | [requirements.txt](file:///G:/My Drive/ACR-AC-RAG/requirements.txt) | All versions pinned with `==` ✅ |

---

## Prioritized Action Plan

### Phase 1 — Critical Security (Today)
| # | Issue | File | Effort |
|---|-------|------|--------|
| C1 | Rotate Google API key | `.env` / GCP Console | 15m |
| C2 | Add DOMPurify to frontend | `index.html` | 1h |
| C3 | Replace pickle with safe format, or add HMAC verification | `rag_engine.py` | 2h |

### Phase 2 — High Priority (This Week)
| # | Issue | File | Effort |
|---|-------|------|--------|
| H1 | Add API key auth to write endpoints | `main.py` | 3h |
| H2 | Sanitize error messages | `main.py` | 1h |
| H3 | Replace `_rag_cache` dict with `TTLCache` | `copilot_engine.py` | 30m |
| H4 | Use context managers for all SQLite connections | `rag_engine.py` | 1h |
| H5 | Add `asyncio.Lock` to `ensure_rag_ready` | `main.py` | 15m |
| H6-H7 | Remove internal errors from health/CDS responses | `main.py` | 30m |

### Phase 3 — Medium Priority (This Sprint)
| # | Issue | File | Effort |
|---|-------|------|--------|
| M1 | Add Unicode normalization to injection patterns | `security_utils.py` | 2h |
| M3 | Fix confidence score semantics (distance → similarity) | `rag_engine.py` | 1h |
| M5 | Add chat history length/size validation | `main.py` | 30m |
| M6 | Add `max_length` to all Pydantic fields | `main.py` | 30m |
| M8 | Add retry logic to `embed_query` | `ingest.py` | 30m |

### Phase 4 — Code Quality (Next Sprint)
| # | Issue | File | Effort |
|---|-------|------|--------|
| P3 | Replace `print()` with `logging` in all engine modules | All engines | 2h |
| L1 | Remove or archive `app.py` | `app.py` | 5m |
| L2 | Extract magic numbers to config constants | `rag_engine.py` | 1h |
| L4 | Clean up stale files | Project root | 15m |
