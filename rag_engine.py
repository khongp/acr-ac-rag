import os
import re
import json
import sqlite3
import pickle
import time
from collections import Counter
from typing import List, Any, Optional
from datetime import datetime
from tenacity import retry, stop_after_attempt, wait_exponential

from langchain_chroma import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from dotenv import load_dotenv
from ingest import CachedGoogleGenerativeAIEmbeddings
from security_utils import INJECTION_PATTERNS as _INJECTION_PATTERNS, redact_phi

load_dotenv()

EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "local").strip().lower()
ENABLE_NLP_EXPANSION = os.getenv("ENABLE_NLP_EXPANSION", "false").strip().lower() == "true"
ENABLE_LLM_RERANK = os.getenv("ENABLE_LLM_RERANK", "true").strip().lower() == "true"
ENABLE_COLBERT_RERANK = os.getenv("ENABLE_COLBERT_RERANK", "false").strip().lower() == "true"
MAX_CANDIDATES = int(os.getenv("MAX_CANDIDATES", "3"))

# Support overriding paths via environment variables for GCS mount compatibility
if EMBEDDING_MODE == "gemini":
    DEFAULT_CHROMA_PATH = "chroma_db_gemini"
else:
    DEFAULT_CHROMA_PATH = "chroma_db_local"

CHROMA_PATH = os.getenv("CHROMA_PATH", DEFAULT_CHROMA_PATH).strip()
CACHE_DB_PATH = os.getenv("CACHE_DB_PATH", "data/query_cache.db").strip()
PROCEDURES_DB_PATH = os.getenv("PROCEDURES_DB_PATH", "data/acr_procedures.db").strip()

CHROMA_SOURCE_PATH = os.getenv("CHROMA_SOURCE_PATH", "").strip()
PROCEDURES_SOURCE_PATH = os.getenv("PROCEDURES_SOURCE_PATH", "").strip()
CACHE_SOURCE_PATH = os.getenv("CACHE_SOURCE_PATH", "").strip()
BM25_RETRIEVER_SOURCE_PATH = os.getenv("BM25_RETRIEVER_SOURCE_PATH", "").strip()
BM25_CHUNKS_SOURCE_PATH = os.getenv("BM25_CHUNKS_SOURCE_PATH", "").strip()


def sync_cache_to_gcs():
    """Copy the query_cache.db file from local storage back to the GCS mount path."""
    if CACHE_SOURCE_PATH and CACHE_DB_PATH:
        if os.path.exists(CACHE_DB_PATH):
            try:
                os.makedirs(os.path.dirname(CACHE_SOURCE_PATH), exist_ok=True)
                import shutil
                shutil.copy2(CACHE_DB_PATH, CACHE_SOURCE_PATH)
                print(f"[SYNC] Successfully backed up query cache DB to GCS mount: {CACHE_SOURCE_PATH}")
            except Exception as e:
                print(f"[WARN] Error backing up query cache DB to GCS mount: {e}")



# redact_phi is imported from security_utils



def get_db_connection(db_path: str) -> sqlite3.Connection:
    """
    Establish a SQLite database connection with a 30-second timeout 
    and WAL (Write-Ahead Logging) enabled for concurrency support.
    """
    conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
    except Exception as e:
        print(f"[WARN] Failed to enable WAL mode: {e}")
    return conn


def init_cache_db():
    """Ensure query cache and overrides tables exist in SQLite database."""
    os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
    conn = get_db_connection(CACHE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS query_cache (
            query_key TEXT PRIMARY KEY,
            recommendation TEXT,
            sources TEXT,
            created_at TEXT
        )
    """)
    # Add created_at column to existing table if it's missing (migration support)
    try:
        cursor.execute("PRAGMA table_info(query_cache)")
        columns = [row[1] for row in cursor.fetchall()]
        if "created_at" not in columns:
            cursor.execute("ALTER TABLE query_cache ADD COLUMN created_at TEXT")
    except Exception as e:
        print(f"[WARN] Error running database migration: {e}")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clinician_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            query_key TEXT,
            original_recommendation TEXT,
            overridden_recommendation TEXT,
            override_reason TEXT,
            clinician_notes TEXT
        )
    """)
    conn.commit()
    conn.close()


def add_clinician_override(query_key: str, original: str, overridden: str, reason: str, notes: str):
    """Log a clinician override to the SQLite database."""
    from datetime import datetime
    try:
        # Redact PHI from clinician notes and query key
        redacted_query = redact_phi(query_key)
        redacted_notes = redact_phi(notes)
        
        conn = get_db_connection(CACHE_DB_PATH)
        cursor = conn.cursor()
        ts = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO clinician_overrides (timestamp, query_key, original_recommendation, overridden_recommendation, override_reason, clinician_notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ts, redacted_query.strip().lower(), original, overridden, reason, redacted_notes))
        conn.commit()
        conn.close()
        print(f"[OVERRIDE] Saved override for query '{redacted_query}' (Reason: {reason})")
        sync_cache_to_gcs()
    except Exception as e:
        print(f"[WARN] Error saving clinician override: {e}")


def get_clinician_overrides() -> list:
    """Retrieve the audit history of clinician overrides from SQLite."""
    try:
        conn = get_db_connection(CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, timestamp, query_key, original_recommendation, overridden_recommendation, override_reason, clinician_notes
            FROM clinician_overrides ORDER BY id DESC LIMIT 50
        """)
        rows = cursor.fetchall()
        conn.close()
        
        overrides = []
        for r in rows:
            overrides.append({
                "id": r[0],
                "timestamp": r[1],
                "query_key": r[2],
                "original_recommendation": r[3],
                "overridden_recommendation": r[4],
                "override_reason": r[5],
                "clinician_notes": r[6]
            })
        return overrides
    except Exception as e:
        print(f"[WARN] Error fetching clinician overrides: {e}")
        return []


def get_cached_query(clinical_scenario: str) -> Optional[dict]:
    """Retrieve RAG response from cache if exists and not expired."""
    cache_key = redact_phi(clinical_scenario).strip().lower()
    try:
        conn = get_db_connection(CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT recommendation, sources, created_at FROM query_cache WHERE query_key = ?", (cache_key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            recommendation, sources_json, created_at = row
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at)
                    if (datetime.now() - dt).days > 7:
                        print(f"[CACHE EXPIRED] SQLite cache row older than 7 days: {cache_key}")
                        return None
                except Exception as e:
                    print(f"[WARN] Error parsing cache timestamp: {e}")
            return {
                "recommendation": recommendation,
                "sources": json.loads(sources_json)
            }
    except Exception as e:
        print(f"[WARN] Error reading cache: {e}")
    return None


def set_cached_query(clinical_scenario: str, result: dict):
    """Write RAG response to SQLite persistent cache with created_at timestamp."""
    cache_key = redact_phi(clinical_scenario).strip().lower()
    try:
        conn = get_db_connection(CACHE_DB_PATH)
        cursor = conn.cursor()
        ts = datetime.now().isoformat()
        cursor.execute(
            "INSERT OR REPLACE INTO query_cache (query_key, recommendation, sources, created_at) VALUES (?, ?, ?, ?)",
            (cache_key, result["recommendation"], json.dumps(result["sources"]), ts)
        )
        conn.commit()
        conn.close()
        sync_cache_to_gcs()
    except Exception as e:
        print(f"[WARN] Error writing cache: {e}")


_cached_topics = None
_cached_key_terms = None

def get_all_topics_keys() -> set:
    global _cached_topics
    if _cached_topics is not None:
        return _cached_topics
    
    _cached_topics = set()
    try:
        conn = get_db_connection(PROCEDURES_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT topic_key FROM acr_procedures")
        rows = cursor.fetchall()
        conn.close()
        _cached_topics = {row[0].strip().lower() for row in rows if row[0]}
    except Exception as e:
        print(f"[ERR] Error fetching topic keys: {e}")
    return _cached_topics

def get_all_topic_key_terms() -> set:
    global _cached_key_terms
    if _cached_key_terms is not None:
        return _cached_key_terms
        
    _cached_key_terms = set()
    stopwords = {
        "and", "the", "for", "with", "after", "before", "known", "suspected", 
        "management", "treatment", "planning", "follow-up", "follow", "evaluation", 
        "imaging", "screening", "assessment", "adult", "child", "infant", "pediatric", 
        "routine", "common", "suspicion", "possible", "diagnostic", "radiologic", "disease"
    }
    topic_keys = get_all_topics_keys()
    for t_key in topic_keys:
        words = re.findall(r"\b\w{4,}\b", t_key)
        for w in words:
            if w not in stopwords:
                _cached_key_terms.add(w)
    return _cached_key_terms

def _expand_abbreviations_locally(query: str) -> str:
    """
    Expands common medical abbreviations and shorthand in the query locally
    using a dictionary map, preserving the original terms.
    """
    if not query:
        return ""
        
    abbrev_map = {
        "LBP": "low back pain",
        "PE": "pulmonary embolism",
        "RUQ": "right upper quadrant",
        "LUQ": "left upper quadrant",
        "RLQ": "right lower quadrant",
        "LLQ": "left lower quadrant",
        "DVT": "deep vein thrombosis",
        "AAA": "abdominal aortic aneurysm",
        "CAD": "coronary artery disease",
        "MVC": "motor vehicle collision",
        "UTI": "urinary tract infection",
        "URI": "upper respiratory infection",
        "AMS": "altered mental status",
        "COPD": "chronic obstructive pulmonary disease",
        "TIA": "transient ischemic attack",
        "r/o": "rule out",
        "ro": "rule out",
        "sob": "shortness of breath",
        "cxr": "chest x-ray",
        "ct": "computed tomography",
        "mri": "magnetic resonance imaging",
        "us": "ultrasound",
        "hx": "history",
        "dx": "diagnosis",
        "tx": "treatment"
    }
    
    expanded = query
    for abbrev, replacement in abbrev_map.items():
        pattern = re.compile(rf"\b{re.escape(abbrev)}\b", re.IGNORECASE)
        if pattern.search(expanded):
            expanded = pattern.sub(f"{abbrev} ({replacement})", expanded)
            
    return expanded

_cross_encoder = None


def _is_therapeutic_query(query: str) -> bool:
    """Classifies if a query is seeking treatment or interventional therapy."""
    query_lower = query.lower()
    treatment_keywords = {
        "treat", "treatment", "therapy", "management", "manage", "how to treat", "how to manage", 
        "fix", "intervention", "interventional", "embolization", "embolise", "ligation", 
        "surgery", "surgical", "pleurodesis", "ir procedure", "ir management"
    }
    return any(kw in query_lower for kw in treatment_keywords)


def _is_therapeutic_candidate(procedures: list) -> bool:
    """Classifies if a candidate is therapeutic based on its procedures."""
    therapeutic_keywords = {
        "embolization", "embolisation", "ligation", "therapy", "modification", "pleurodesis", 
        "thrombectomy", "recanalization", "ablation", "surgery", "surgical", "resection", "stenting",
        "angioplasty", "bypass", "revascularization"
    }
    for proc in procedures:
        proc_lower = proc.get("Procedure", "").lower()
        if any(kw in proc_lower for kw in therapeutic_keywords):
            return True
    return False


def get_cross_encoder():
    global _cross_encoder
    if _cross_encoder is None:
        try:
            from sentence_transformers import CrossEncoder
            print("[RERANK] Loading local CrossEncoder: cross-encoder/ms-marco-MiniLM-L-6-v2 on CPU...")
            _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2", device="cpu")
        except ImportError:
            print("[RERANK WARNING] sentence_transformers is not installed. Skipping local CrossEncoder reranking.")
            _cross_encoder = "unavailable"
    return _cross_encoder if _cross_encoder != "unavailable" else None


def init_procedures_db():
    """Ensure the acr_procedures table exists and is populated in SQLite."""
    os.makedirs(os.path.dirname(PROCEDURES_DB_PATH), exist_ok=True)
    conn = get_db_connection(PROCEDURES_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS acr_procedures (
            topic_key TEXT,
            scenario_key TEXT,
            variant_json TEXT
        )
    """)
    # Check if table already has rows, and check if count matches JSON count of entries
    json_path = "data/acr_variant_tables.json"
    needs_population = False
    data = []
    
    try:
        cursor.execute("SELECT COUNT(*) FROM acr_procedures")
        count = cursor.fetchone()[0]
        if count == 0:
            needs_population = True
        else:
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                json_variants_count = sum(len(topic.get("variantData", [])) for topic in data)
                if count != json_variants_count:
                    print(f"[INIT] Procedures database count mismatch (SQLite: {count}, JSON: {json_variants_count}). Rebuilding...")
                    cursor.execute("DELETE FROM acr_procedures")
                    needs_population = True
    except Exception:
        needs_population = True

    if needs_population:
        if os.path.exists(json_path):
            if not data:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            
            print(f"[INIT] Populating SQLite procedures database from {json_path}...")
            insert_data = []
            for topic in data:
                topic_name = topic.get("topicName", "").strip()
                for variant in topic.get("variantData", []):
                    scenario = variant.get("Scenario", "").strip()
                    if not scenario:
                        continue
                    topic_key = topic_name.lower()
                    scenario_key = scenario.lower()
                    variant_json = json.dumps(variant)
                    insert_data.append((topic_key, scenario_key, variant_json))
            
            cursor.executemany(
                "INSERT INTO acr_procedures (topic_key, scenario_key, variant_json) VALUES (?, ?, ?)",
                insert_data
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_acr_procedures ON acr_procedures (topic_key, scenario_key)")
            conn.commit()
            print(f"[INIT] Populated {len(insert_data)} rows in SQLite procedures database.")
            
            # Reset cached topic keys/terms so they reload with the new database content
            global _cached_topics, _cached_key_terms
            _cached_topics = None
            _cached_key_terms = None
            
    conn.close()


def get_procedures_from_db(topic: str, scenario: str) -> list:
    """Retrieve procedures from SQLite procedures table."""
    procedures = []
    try:
        conn = get_db_connection(PROCEDURES_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT variant_json FROM acr_procedures WHERE topic_key = ? AND scenario_key = ?",
            (topic.lower(), scenario.lower())
        )
        rows = cursor.fetchall()
        conn.close()
        for row in rows:
            procedures.append(json.loads(row[0]))
    except Exception as e:
        print(f"[ERR] Error querying SQLite procedures: {e}")
    return procedures


def _extract_scenario(content: str) -> str:
    """Extract the 'Clinical Scenario (Variant)' value from a table doc's page_content."""
    m = re.search(r"Clinical Scenario \(Variant\):\s*(.+?)(?:\n|$)", content, re.IGNORECASE)
    if m:
        return m.group(1).strip().lower()
    return ""


def _extract_topic(content: str) -> str:
    """Extract the 'Topic' value from a table doc's page_content."""
    m = re.search(r"Topic:\s*(.+?)(?:\n|Clinical Scenario)", content, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


_bm25_retriever = None

def reciprocal_rank_fusion(doc_lists: List[List[Document]], k: int = 60) -> List[Document]:
    """
    Fuses multiple ranked lists of documents using Reciprocal Rank Fusion.
    """
    scores = {}
    doc_by_id = {}
    for doc_list in doc_lists:
        for rank, doc in enumerate(doc_list):
            doc_id = doc.page_content
            doc_by_id[doc_id] = doc
            if doc_id not in scores:
                scores[doc_id] = 0.0
            scores[doc_id] += 1.0 / (k + (rank + 1))
            
    sorted_docs = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    return [doc_by_id[doc_id] for doc_id in sorted_docs]


def rerank_with_colbert(query: str, docs: List[Document], top_k: int = 5) -> List[Document]:
    """
    Re-rank RRF-fused results using ColBERT late-interaction scoring.
    Attempts to import ragatouille. If not available, falls back to input ranking.
    """
    if not docs:
        return []
    
    try:
        from ragatouille import RAGPretrainedModel
        print(f"[COLBERT] Loading pretrained ColBERTv2 model to rerank {len(docs)} documents...")
        colbert = RAGPretrainedModel.from_pretrained("colbert-ir/colbertv2.0")
        
        passages = [doc.page_content for doc in docs]
        results = colbert.rerank(query=query, documents=passages, k=top_k)
        
        reranked_docs = []
        for res in results:
            content = res["content"]
            for doc in docs:
                if doc.page_content == content:
                    doc.metadata["colbert_score"] = res["score"]
                    reranked_docs.append(doc)
                    break
        return reranked_docs
    except ImportError:
        print("[WARN] ragatouille package not installed. Skipping ColBERT reranking.")
        return docs[:top_k]
    except Exception as e:
        print(f"[WARN] ColBERT reranking failed: {e}. Falling back.")
        return docs[:top_k]


def load_bm25_retriever():
    """Loads the pre-built BM25 index from disk with fallbacks."""
    bm25_path = "data/bm25_retriever.pkl"
    if os.path.exists(bm25_path):
        print(f"Loading BM25 Retriever from {bm25_path}...")
        try:
            with open(bm25_path, "rb") as f:
                retriever = pickle.load(f)
            retriever.k = 50
            return retriever
        except Exception as e:
            print(f"[WARN] Error loading BM25 pickle: {e}")
            
    # Try rebuild fallback
    chunks_path = "data/bm25_chunks.pkl"
    if os.path.exists(chunks_path):
        print(f"Rebuilding BM25 Retriever from chunks at {chunks_path}...")
        try:
            from langchain_community.retrievers import BM25Retriever
            with open(chunks_path, "rb") as f:
                chunks = pickle.load(f)
            retriever = BM25Retriever.from_documents(chunks)
            retriever.k = 50
            return retriever
        except Exception as e:
            print(f"[WARN] Error rebuilding BM25: {e}")
            
    print("[WARN] BM25 index not found. Hybrid search will fallback to vector-only.")
    return None


def _rerank_scenarios_llm(query: str, candidates: List[tuple]) -> List[tuple]:
    """
    Uses Gemini to rerank candidate topics and scenarios based on clinical relevance.
    Returns the top MAX_CANDIDATES most clinically relevant candidate scenarios.
    """
    if not candidates:
        return []
    if len(candidates) <= MAX_CANDIDATES:
        return candidates
        
    try:
        from pydantic import BaseModel, Field
        from typing import List
        
        class SelectedCandidate(BaseModel):
            topic: str = Field(description="The exact Topic name from the candidate list")
            scenario: str = Field(description="The exact Scenario (Variant) name from the candidate list")
            rationale: str = Field(description="1-sentence explanation of why this fits the clinical presentation")
            
        class RerankedOutput(BaseModel):
            rankings: List[SelectedCandidate] = Field(description="Top 3 selected candidate scenarios, in order of clinical relevance")
            
        global _llm_fast
        if _llm_fast is None:
            from llm_router import get_llm_fast
            _llm_fast = get_llm_fast()
        structured_llm = _llm_fast.with_structured_output(RerankedOutput)
        
        candidates_str = ""
        for idx, (t, s) in enumerate(candidates):
            procs = get_procedures_from_db(t, s)
            proc_names = [p.get("Procedure", "") for p in procs]
            proc_str = ", ".join(proc_names)
            candidates_str += f"{idx+1}. Topic: '{t}' | Scenario: '{s}' | Procedures: {proc_str}\n"
            
        prompt = (
            "You are a medical guidelines reranking agent.\n"
            "Given the patient's clinical scenario, review the list of candidate ACR Appropriateness Criteria topics/scenarios "
            "and select the top 3 most clinically relevant ones, ranked from most relevant to least relevant.\n"
            "Do not modify the topic or scenario names, they must match the input candidates exactly.\n\n"
            f"Patient Scenario: {query}\n\n"
            "Candidate List:\n"
            f"{candidates_str}\n"
            "Output the top 3 rankings:"
        )
        
        res = _invoke_with_retry(structured_llm, prompt)
        
        selected = []
        for rank in res.rankings:
            matched_pair = None
            for t, s in candidates:
                if t.lower() == rank.topic.lower() and s.lower() == rank.scenario.lower():
                    matched_pair = (t, s)
                    break
            if matched_pair and matched_pair not in selected:
                selected.append(matched_pair)
                
        if not selected:
            print("[WARN] LLM reranker failed to match any candidates. Falling back to default order.")
            return candidates[:MAX_CANDIDATES]
            
        return selected[:MAX_CANDIDATES]
        
    except Exception as e:
        print(f"[WARN] LLM reranker failed: {e}. Falling back to default vector/BM25 rank.")
        return candidates[:MAX_CANDIDATES]


def _calculate_medical_boost(query: str, topic: str, scenario: str) -> float:
    query_lower = query.lower()
    topic_lower = topic.lower()
    scenario_lower = scenario.lower()
    boost = 0.0

    # 1. Acute Aortic Syndrome / Aortic Dissection pathognomonic boosting
    if any(k in query_lower for k in ["tearing", "aortic", "aorta", "dissection"]):
        if "acute aortic syndrome" in topic_lower or "acute aortic syndrome" in scenario_lower:
            boost += 2.0
            
    # 2. Testicular torsion / Acute scrotal pain pathognomonic boosting
    if any(k in query_lower for k in ["testicular", "scrotal", "scrotum", "testis", "cremasteric", "torsion"]):
        if any(k in query_lower for k in ["acute", "sudden", "suddenly", "pain", "hours ago"]):
            if "scrotal pain" in topic_lower or "scrotal pain" in scenario_lower:
                boost += 2.0
            elif "testicular cancer" in topic_lower or "testicular cancer" in scenario_lower:
                boost -= 2.0  # Penalize cancer surveillance for acute scrotal pain presentation
                
    return boost


class CombinedTypeRetriever(BaseRetriever):
    db: Any
    embeddings: Any
    bm25_retriever: Any = None
    k_tables: int = 30
    k_narrative: int = 3

    class Config:
        arbitrary_types_allowed = True

    def _search_vector_tables(self, query: str, query_emb: Optional[List[float]]) -> List[Document]:
        vector_tables = []
        if query_emb is not None:
            try:
                vector_tables_with_scores = self.db.similarity_search_with_score(
                    query, k=self.k_tables, filter={"type": "variant_table"}
                )
                for doc, score in vector_tables_with_scores:
                    doc.metadata["score"] = float(score)
                    doc.metadata["retrieval_method"] = "vector"
                    vector_tables.append(doc)
            except Exception as e:
                print(f"[WARN] Vector table search with score failed: {e}. Falling back to standard search.")
                try:
                    vector_tables = self.db.similarity_search_by_vector(
                        query_emb, k=self.k_tables, filter={"type": "variant_table"}
                    )
                    for doc in vector_tables:
                        doc.metadata["score"] = 1.0
                        doc.metadata["retrieval_method"] = "vector"
                except Exception as ex:
                    print(f"[ERROR] Vector table search by vector failed: {ex}")
        return vector_tables

    def _search_bm25_tables(self, query: str) -> List[Document]:
        bm25_tables = []
        if self.bm25_retriever:
            try:
                bm25_docs = self.bm25_retriever.invoke(query)
                bm25_tables = [d for d in bm25_docs if d.metadata.get("type") == "variant_table"]
                for doc in bm25_tables:
                    doc.metadata["retrieval_method"] = "bm25"
                    doc.metadata["score"] = doc.metadata.get("score", 1.0)
            except Exception as e:
                print(f"[WARN] BM25 table query failed: {e}")
        return bm25_tables

    def _search_vector_narratives(self, query: str, query_emb: Optional[List[float]], candidate_topic_names: List[str]) -> List[Document]:
        vector_narratives = []
        if query_emb is not None:
            # Try topic-constrained narrative search first
            if candidate_topic_names:
                try:
                    topic_filter = {
                        "$and": [
                            {"type": "narrative"},
                            {"topic": {"$in": candidate_topic_names}}
                        ]
                    }
                    vector_narratives_with_scores = self.db.similarity_search_with_score(
                        query, k=self.k_narrative, filter=topic_filter
                    )
                    for doc, score in vector_narratives_with_scores:
                        doc.metadata["score"] = float(score)
                        doc.metadata["retrieval_method"] = "vector"
                        vector_narratives.append(doc)
                    if vector_narratives:
                        print(f"[NARRATIVE] Topic-constrained search returned {len(vector_narratives)} chunks for topics: {candidate_topic_names}")
                except Exception as e:
                    print(f"[WARN] Topic-constrained narrative search failed: {e}. Falling back to unfiltered search.")
            
            # Fallback: unfiltered narrative search if topic-constrained returned nothing
            if not vector_narratives:
                try:
                    vector_narratives_with_scores = self.db.similarity_search_with_score(
                        query, k=self.k_narrative, filter={"type": "narrative"}
                    )
                    for doc, score in vector_narratives_with_scores:
                        doc.metadata["score"] = float(score)
                        doc.metadata["retrieval_method"] = "vector"
                        vector_narratives.append(doc)
                    print(f"[NARRATIVE] Unfiltered fallback search returned {len(vector_narratives)} chunks.")
                except Exception as e:
                    print(f"[WARN] Vector narrative search with score failed: {e}. Falling back to standard search.")
                    try:
                        vector_narratives = self.db.similarity_search_by_vector(
                            query_emb, k=self.k_narrative, filter={"type": "narrative"}
                        )
                        for doc in vector_narratives:
                            doc.metadata["score"] = 1.0
                            doc.metadata["retrieval_method"] = "vector"
                    except Exception as ex:
                        print(f"[ERROR] Vector narrative search by vector failed: {ex}")
        return vector_narratives

    def _search_bm25_narratives(self, query: str, candidate_topic_names: List[str]) -> List[Document]:
        bm25_narratives = []
        if self.bm25_retriever:
            try:
                bm25_docs = self.bm25_retriever.invoke(query)
                bm25_narratives = [d for d in bm25_docs if d.metadata.get("type") == "narrative"]
                # Post-filter BM25 narratives to match selected candidate topics
                if candidate_topic_names:
                    filtered_bm25 = [d for d in bm25_narratives if d.metadata.get("topic") in candidate_topic_names]
                    if filtered_bm25:
                        bm25_narratives = filtered_bm25
                        print(f"[NARRATIVE] BM25 topic-filtered to {len(bm25_narratives)} chunks.")
                for doc in bm25_narratives:
                    doc.metadata["retrieval_method"] = "bm25"
                    doc.metadata["score"] = doc.metadata.get("score", 1.0)
            except Exception as e:
                print(f"[WARN] BM25 narrative query failed: {e}")
        return bm25_narratives

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        from concurrent.futures import ThreadPoolExecutor

        # ── Step 1: Query embedding ──
        query_emb = None
        try:
            query_emb = self.embeddings.embed_query(query)
        except Exception as e:
            print(f"[ERROR] Embedding query failed: {e}. Vector search will be bypassed.")

        # ── Step 2: Probe table docs for scenario detection (Hybrid Search) in Parallel ──
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_vector = executor.submit(self._search_vector_tables, query, query_emb)
            future_bm25 = executor.submit(self._search_bm25_tables, query)
            
            vector_tables = future_vector.result()
            bm25_tables = future_bm25.result()
            
        # Reciprocal Rank Fusion
        fused_tables = reciprocal_rank_fusion([vector_tables, bm25_tables])

        if ENABLE_COLBERT_RERANK:
            fused_tables = rerank_with_colbert(query, fused_tables, top_k=15)

        # ── Step 3: Scenario detection ──
        unique_candidates = []
        for doc in fused_tables:
            sc = _extract_scenario(doc.page_content)
            tp = _extract_topic(doc.page_content)
            if sc and tp:
                pair = (tp, sc)
                if pair not in unique_candidates:
                    unique_candidates.append(pair)

        # Apply age-based demographic pre-filtering/boosting
        is_pediatric = False
        age_match = re.search(r"\b(\d{1,3})\s*(?:yo|year|yr|-year|years\s+old)\b", query, re.IGNORECASE)
        if age_match:
            is_pediatric = (int(age_match.group(1)) < 18)
        elif any(x in query.lower() for x in ["child", "pediatric", "infant", "peds", "boy", "girl", "neonat"]):
            is_pediatric = True
            
        print(f"[DEMOGRAPHICS] Query classified as: {'Pediatric' if is_pediatric else 'Adult'}")
        
        filtered_candidates = []
        for tp, sc in unique_candidates:
            tp_lower = tp.lower()
            is_child_topic = "child" in tp_lower or "pediatric" in tp_lower or "infant" in tp_lower or "neonat" in tp_lower
            
            if is_pediatric:
                if is_child_topic:
                    filtered_candidates.insert(0, (tp, sc))  # Prioritize pediatric guidelines
                else:
                    filtered_candidates.append((tp, sc))
            else:
                if is_child_topic and ("-child" in tp_lower or "child" in tp_lower):
                    print(f"[DEMOGRAPHICS] Filtering out pediatric topic '{tp}' for adult query.")
                    continue
                filtered_candidates.append((tp, sc))
        unique_candidates = filtered_candidates

        # Rerank candidates using local Cross-Encoder (default) or LLM
        if ENABLE_LLM_RERANK:
            print(f"[RERANK] Prompting LLM to rerank {len(unique_candidates)} unique candidates...")
            best_candidates = _rerank_scenarios_llm(query, unique_candidates)
            print(f"[RERANK] Selected top {len(best_candidates)} candidates.")
        else:
            if unique_candidates:
                try:
                    ce = get_cross_encoder()
                    if ce is None:
                        raise ValueError("sentence_transformers is not installed")
                    pairs = []
                    query_is_therapeutic = _is_therapeutic_query(query)
                    candidate_attributes = []
                    
                    for tp, sc in unique_candidates:
                        procs = get_procedures_from_db(tp, sc)
                        proc_names = [p.get("Procedure", "") for p in procs]
                        proc_str = ", ".join(proc_names)
                        
                        # Enrich context with procedures
                        text = f"Topic: {tp}. Clinical Scenario: {sc}. Procedures: {proc_str}."
                        pairs.append((query, text))
                        
                        is_ther = _is_therapeutic_candidate(procs)
                        candidate_attributes.append(is_ther)
                        
                    scores = ce.predict(pairs)
                    
                    # Apply clinical intent alignment boosting and medical clinical boosts
                    boosted_scores = []
                    for idx, score in enumerate(scores):
                        boost = 0.0
                        is_ther = candidate_attributes[idx]
                        if query_is_therapeutic:
                            if is_ther:
                                boost += 1.0  # Boost therapeutic candidates for therapeutic queries
                        else:
                            if not is_ther:
                                boost += 1.0  # Boost diagnostic candidates for diagnostic queries
                                
                        # Apply pathognomonic medical boosts
                        tp, sc = unique_candidates[idx]
                        boost += _calculate_medical_boost(query, tp, sc)
                        
                        boosted_scores.append(score + boost)
                        
                    scored_candidates = sorted(zip(unique_candidates, boosted_scores), key=lambda x: x[1], reverse=True)
                    unique_candidates = [cand for cand, score in scored_candidates]
                    print(f"[LOCAL-RERANK] Scored and reranked {len(unique_candidates)} candidates using ms-marco-MiniLM-L-6-v2 with procedure enrichment & boosting.")
                except Exception as e:
                    print(f"[WARN] Local Cross-Encoder reranking failed: {e}. Using default order.")
            best_candidates = unique_candidates[:MAX_CANDIDATES]
        
        scenario_tables = []
        if best_candidates:
            print(f"[ROUTER-COMBINED] Loading procedures for top {len(best_candidates)} candidates...")
            for best_topic, best_scenario in best_candidates:
                # ── Step 4: Look up detailed procedures from SQLite ──
                procedures = get_procedures_from_db(best_topic, best_scenario)
                
                for row in procedures:
                    proc = row.get("Procedure", "")
                    cat = row.get("Appropriateness Category", "")
                    adult_rrl = row.get("Adult RRL", "")
                    peds_rrl = row.get("Peds RRL", "")
                    
                    # Explicitly label missing RRL for interventional procedures
                    if not adult_rrl or adult_rrl.strip() == "":
                        adult_rrl = "Not Applicable (Interventional Procedure)"
                    if not peds_rrl or peds_rrl.strip() == "":
                        peds_rrl = "Not Applicable"
                    
                    content = f"ACR Appropriateness Table Data:\n"
                    content += f"Topic: {best_topic}\n"
                    content += f"Clinical Scenario (Variant): {best_scenario}\n"
                    content += f"Procedure: {proc}\n"
                    content += f"Appropriateness Category: {cat}\n"
                    content += f"Adult Radiation Dose (RRL): {adult_rrl}\n"
                    content += f"Pediatric Radiation Dose (RRL): {peds_rrl}\n"
                        
                    # Find source score if available from fused tables
                    matching_score = 1.0
                    for t_doc in fused_tables:
                        if _extract_scenario(t_doc.page_content) == best_scenario.lower() and _extract_topic(t_doc.page_content).lower() == best_topic.lower():
                            matching_score = t_doc.metadata.get("score", 1.0)
                            break
                            
                    scenario_tables.append(Document(
                        page_content=content,
                        metadata={
                            "source": "acr_variant_tables.json",
                            "type": "variant_table",
                            "topic": best_topic,
                            "scenario": best_scenario,
                            "score": matching_score,
                            "retrieval_method": "hybrid_db"
                        }
                    ))
        else:
            print("[WARN] No scenario matched in probe. Using vector probe documents directly.")
            scenario_tables = vector_tables[:5]

        # ── Step 5: Get narratives via topic-constrained hybrid search in Parallel ──
        candidate_topic_names = list(set(tp for tp, sc in best_candidates)) if best_candidates else []
        
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_vector_narratives = executor.submit(self._search_vector_narratives, query, query_emb, candidate_topic_names)
            future_bm25_narratives = executor.submit(self._search_bm25_narratives, query, candidate_topic_names)
            
            vector_narratives = future_vector_narratives.result()
            bm25_narratives = future_bm25_narratives.result()
            
        fused_narratives = reciprocal_rank_fusion([vector_narratives, bm25_narratives])
        if ENABLE_COLBERT_RERANK:
            best_narratives = rerank_with_colbert(query, fused_narratives, top_k=self.k_narrative)
        else:
            if fused_narratives:
                try:
                    ce = get_cross_encoder()
                    if ce is None:
                        raise ValueError("sentence_transformers is not installed")
                    # Rerank the top 10 fused candidates to save computation
                    candidates_to_rerank = fused_narratives[:10]
                    pairs = [(query, doc.page_content) for doc in candidates_to_rerank]
                    scores = ce.predict(pairs)
                    scored_docs = sorted(zip(candidates_to_rerank, scores), key=lambda x: x[1], reverse=True)
                    best_narratives = [doc for doc, score in scored_docs][:self.k_narrative]
                    print(f"[LOCAL-RERANK] Scored and reranked {len(candidates_to_rerank)} narrative chunks using ms-marco-MiniLM-L-6-v2.")
                except Exception as e:
                    print(f"[WARN] Local Cross-Encoder narrative reranking failed: {e}. Using default order.")
                    best_narratives = fused_narratives[:self.k_narrative]
            else:
                best_narratives = []

        return scenario_tables + best_narratives


def get_retriever():
    """Build the retrieval pipeline using the selected embedding model and ChromaDB."""
    from llm_router import get_embeddings
    embeddings = get_embeddings()
    
    print(f"Loading ChromaDB Vector Store from {CHROMA_PATH}...")
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)
    
    return CombinedTypeRetriever(
        db=db,
        embeddings=embeddings,
        bm25_retriever=_bm25_retriever,
        k_tables=30,
        k_narrative=3,
    )


def get_llm():
    from llm_router import get_llm as router_get_llm
    return router_get_llm(temperature=0.0)


# PEA architecture prompt
template = """
You are an AI assistant designed exclusively to retrieve and summarize the published American College of Radiology (ACR) Appropriateness Criteria.
Please do not provide specific medical advice, independent diagnoses, or prescriptive treatment plans.
You must guide the user toward consulting with a licensed healthcare professional.
You must offer general, non-prescriptive information grounded ONLY in the provided text context.

IMPORTANT: 
1. If the context contains 'ACR Appropriateness Table Data' that matches the user's clinical presentation, list the procedures or imaging modalities rated "Usually appropriate" (Ratings 7-9) followed by "May be appropriate" (Ratings 4-6). Include Radiation Dose (RRL) when applicable. For interventional procedures without radiation, state 'Not Applicable'.
2. If the context DOES NOT contain the exact table data, but DOES contain narrative text or guidelines relevant to the clinical scenario, you MUST summarize that narrative guidance. Do not simply refuse to answer.
3. If context comes from multiple guideline topics, clearly label which recommendations come from which guideline topic.
4. Then, use any narrative text provided to add a brief 'Clinical Rationale / FYI' section explaining why.
5. Keep the output extremely concise and direct. Avoid verbose explanations or conversational filler. Be as brief as possible while providing the required information.

Context:
{context}

Question:
{question}

Please structure your response using the Plan -> Estimate -> Answer (PEA) format:
- Plan: 1-3 bullet points detailing exactly how you intend to approach the clinical query based solely on the retrieved ACR guidelines.
- Estimate/Rationale: State the likely clinical pathway and provide a rationale strictly grounded in the retrieved text (especially the 'Clinical Rationale / FYI').
- Final: Provide the definitive recommendation. If variant tables were found, list them here. If only narrative guidance was found, summarize the clinical recommendations here.

If the context contains absolutely no relevant information for the scenario, state "I cannot answer this based on the provided ACR guidelines."
"""

prompt = PromptTemplate.from_template(template)


def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)


# Singleton Pattern for RAG Components
_retriever = None
_llm = None
_llm_fast = None
_chain = None


def init_rag():
    """Initialize all RAG components (embedding model, vector store, LLM).
    Safe to call multiple times — uses singleton pattern."""
    global _retriever, _llm, _llm_fast, _chain, _bm25_retriever
    
    if _retriever is not None and _llm is not None and _llm_fast is not None and _chain is not None:
        return
        
    # 1. Copy database files from GCS source mounts to fast local /tmp storage on Cloud Run
    if CHROMA_SOURCE_PATH and os.path.exists(CHROMA_SOURCE_PATH):
        if not os.path.exists(CHROMA_PATH):
            import shutil
            import uuid
            temp_chroma_path = f"{CHROMA_PATH}_tmp_{uuid.uuid4().hex}"
            print(f"[STARTUP] Copying ChromaDB from GCS mount {CHROMA_SOURCE_PATH} to temp storage {temp_chroma_path}...")
            try:
                shutil.copytree(CHROMA_SOURCE_PATH, temp_chroma_path)
                os.rename(temp_chroma_path, CHROMA_PATH)
                print("[STARTUP] ChromaDB copy completed atomically.")
            except (FileExistsError, OSError):
                print(f"[STARTUP] Another worker already created {CHROMA_PATH}. Cleaning up temp copy.")
                shutil.rmtree(temp_chroma_path, ignore_errors=True)
            except Exception as e:
                shutil.rmtree(temp_chroma_path, ignore_errors=True)
                if not os.path.exists(CHROMA_PATH):
                    raise e
            
    if PROCEDURES_SOURCE_PATH and os.path.exists(PROCEDURES_SOURCE_PATH):
        if not os.path.exists(PROCEDURES_DB_PATH):
            import shutil
            import uuid
            temp_db_path = f"{PROCEDURES_DB_PATH}_tmp_{uuid.uuid4().hex}"
            print(f"[STARTUP] Copying procedures DB from {PROCEDURES_SOURCE_PATH} to temp {temp_db_path}...")
            try:
                os.makedirs(os.path.dirname(temp_db_path), exist_ok=True)
                shutil.copy2(PROCEDURES_SOURCE_PATH, temp_db_path)
                os.makedirs(os.path.dirname(PROCEDURES_DB_PATH), exist_ok=True)
                os.rename(temp_db_path, PROCEDURES_DB_PATH)
                print("[STARTUP] Procedures DB copy completed atomically.")
            except (FileExistsError, OSError):
                print(f"[STARTUP] Another worker already created {PROCEDURES_DB_PATH}. Cleaning up temp copy.")
                if os.path.exists(temp_db_path):
                    os.remove(temp_db_path)
            except Exception as e:
                if os.path.exists(temp_db_path):
                    os.remove(temp_db_path)
                if not os.path.exists(PROCEDURES_DB_PATH):
                    raise e

    if CACHE_SOURCE_PATH and os.path.exists(CACHE_SOURCE_PATH):
        if not os.path.exists(CACHE_DB_PATH):
            import shutil
            import uuid
            temp_cache_path = f"{CACHE_DB_PATH}_tmp_{uuid.uuid4().hex}"
            print(f"[STARTUP] Copying query cache DB from {CACHE_SOURCE_PATH} to temp {temp_cache_path}...")
            try:
                os.makedirs(os.path.dirname(temp_cache_path), exist_ok=True)
                shutil.copy2(CACHE_SOURCE_PATH, temp_cache_path)
                os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
                os.rename(temp_cache_path, CACHE_DB_PATH)
                print("[STARTUP] Query cache DB copy completed atomically.")
            except (FileExistsError, OSError):
                print(f"[STARTUP] Another worker already created {CACHE_DB_PATH}. Cleaning up temp copy.")
                if os.path.exists(temp_cache_path):
                    os.remove(temp_cache_path)
            except Exception as e:
                if os.path.exists(temp_cache_path):
                    os.remove(temp_cache_path)
                if not os.path.exists(CACHE_DB_PATH):
                    raise e

    init_cache_db()
    init_procedures_db()
    # Copy BM25 files from GCS mount if available
    bm25_retriever_path = "data/bm25_retriever.pkl"
    bm25_chunks_path = "data/bm25_chunks.pkl"
    
    if BM25_RETRIEVER_SOURCE_PATH and os.path.exists(BM25_RETRIEVER_SOURCE_PATH):
        if not os.path.exists(bm25_retriever_path):
            import shutil
            print(f"[STARTUP] Copying BM25 retriever from GCS mount {BM25_RETRIEVER_SOURCE_PATH}...")
            try:
                os.makedirs(os.path.dirname(bm25_retriever_path), exist_ok=True)
                shutil.copy2(BM25_RETRIEVER_SOURCE_PATH, bm25_retriever_path)
                print("[STARTUP] BM25 retriever copy completed.")
            except Exception as e:
                print(f"[WARN] Failed to copy BM25 retriever from GCS: {e}")
    
    if BM25_CHUNKS_SOURCE_PATH and os.path.exists(BM25_CHUNKS_SOURCE_PATH):
        if not os.path.exists(bm25_chunks_path):
            import shutil
            print(f"[STARTUP] Copying BM25 chunks from GCS mount {BM25_CHUNKS_SOURCE_PATH}...")
            try:
                os.makedirs(os.path.dirname(bm25_chunks_path), exist_ok=True)
                shutil.copy2(BM25_CHUNKS_SOURCE_PATH, bm25_chunks_path)
                print("[STARTUP] BM25 chunks copy completed.")
            except Exception as e:
                print(f"[WARN] Failed to copy BM25 chunks from GCS: {e}")

    if _bm25_retriever is None:
        _bm25_retriever = load_bm25_retriever()
    if _retriever is None:
        _retriever = get_retriever()
    if _llm is None:
        _llm = get_llm()
    if _llm_fast is None:
        from llm_router import get_llm_fast
        _llm_fast = get_llm_fast()
    if _chain is None:
        _chain = prompt | _llm | StrOutputParser()
    
    # Pre-load CrossEncoder to prevent first-query latency spike
    get_cross_encoder()
    



@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
def _invoke_with_retry(chain_or_llm, input_data):
    return chain_or_llm.invoke(input_data)


def _expand_clinical_query(query: str) -> str:
    """
    Uses Gemini to expand medical abbreviations, shorthand, and acronyms in the query.
    This is extremely cheap and low-latency, and makes keyword and vector RAG matching
    highly robust for real-world clinician inputs.
    """
    try:
        global _llm_fast
        if _llm_fast is None:
            from llm_router import get_llm_fast
            _llm_fast = get_llm_fast()
        prompt = (
            "You are a medical vocabulary expansion agent.\n"
            "Analyze the following clinical query and expand any shorthand, abbreviations, or acronyms "
            "to their full, formal medical names (e.g. resolve LBP to Low Back Pain, MVC to Motor Vehicle Collision, "
            "hx to History, s/p to Status Post, etc.).\n"
            "Do not add any other commentary or introductory text. Return only the expanded clinical text.\n"
            f"\nClinical Query: {query}"
        )
        
        res = _invoke_with_retry(_llm_fast, prompt)
        return res.content.strip()
    except Exception as e:
        print(f"[WARN] LLM Query expansion failed: {e}. Falling back to raw query.")
        return query


# ── RAG Input Sanitization and Completeness (Abstention Gate) ──

def evaluate_input_completeness(clinical_text: str, fhir_bundle: dict = None) -> tuple[bool, str]:
    """
    Evaluate if the clinical text has enough detail (anatomy, symptoms/condition) to search guidelines.
    Returns (should_abstain, reason_text).
    """
    stripped_text = clinical_text.strip()
    if len(stripped_text) < 10:
        return True, "Clinical scenario description is too short to evaluate (must be at least 10 characters)."

    # Map the required 30 body regions / organ systems to common clinical terms, substrings, and synonyms
    body_regions_map = {
        "head": ["head", "skull", "brain", "cerebral", "cerebro", "stroke", "seizure", "neurolog", "dementia", "cognitive", "acoustic", "migraine", "cephaly", "mental"],
        "brain": ["brain", "cerebral", "cerebro", "stroke", "seizure", "neurolog", "dementia", "cognitive", "acoustic"],
        "chest": ["chest", "pleuritic", "rib", "lung", "pulmonary", "bronch", "hilar", "dyspnea", "asbestos", "cough", "cardiac", "heart", "coronary", "aort", "pericarditis", "pneumo"],
        "lung": ["lung", "pulmonary", "bronch", "hilar", "dyspnea", "asbestos", "cough"],
        "abdomen": ["abdomen", "abdominal", "epigastr", "flank", "belly", "gastric", "mesenteric", "cholecystitis", "gallbladder", "jaundice", "biliary", "colic", "ascites", "liver", "hepatic", "pancreas", "pancreatic", "bowel", "intestin", "gut", "rectal", "diverticulitis", "crohn", "perforated"],
        "pelvis": ["pelvis", "pelvic", "adnexal", "testi", "scrot", "groin", "adnexa", "ovary", "ovarian", "uterus", "uterine", "prostate", "prostatic", "endometriosis"],
        "spine": ["spine", "spinal", "back", "radiculopathy", "cervical", "thoracic", "lumbar"],
        "cervical": ["cervical"],
        "thoracic": ["thoracic"],
        "lumbar": ["lumbar"],
        "extremity": ["extremity", "limb", "leg", "arm", "hand", "foot", "finger", "toe", "gout", "claudication", "hip", "knee", "shoulder", "ankle", "wrist", "scaphoid", "malleolus", "patella", "joint", "bone", "fracture"],
        "breast": ["breast", "mammogr"],
        "cardiac": ["cardiac", "heart", "coronary", "pericarditis", "myocard", "valvular", "echo"],
        "vascular": ["vascular", "artery", "arterial", "vein", "venous", "dvt", "carotid", "aort", "renovascular", "stenosis", "claudication", "embolism", "thrombosis", "bleed", "hemorrhage", "ischemia"],
        "hip": ["hip"],
        "knee": ["knee"],
        "shoulder": ["shoulder"],
        "ankle": ["ankle"],
        "wrist": ["wrist"],
        "neck": ["neck", "thyroid", "cervical"],
        "liver": ["liver", "hepatic", "biliary", "jaundice", "cholecystitis"],
        "kidney": ["kidney", "renal", "nephro", "urolithiasis", "colic"],
        "pancreas": ["pancreas", "pancreatic"],
        "bowel": ["bowel", "intestin", "gut", "rectal", "diverticulitis", "crohn", "perforated"],
        "colon": ["colon", "colorectal"],
        "bladder": ["bladder", "cystitis"],
        "prostate": ["prostate", "prostatic"],
        "uterus": ["uterus", "uterine"],
        "ovary": ["ovary", "ovarian"]
    }
    
    text_lower = stripped_text.lower()
    has_body_region = False
    for region, patterns in body_regions_map.items():
        if any(pat in text_lower for pat in patterns):
            has_body_region = True
            break

    # Fallback check: if the query contains any descriptive terms from the known guideline topics
    if not has_body_region:
        try:
            topic_terms = get_all_topic_key_terms()
            words_in_query = re.findall(r"\b\w{4,}\b", text_lower)
            if any(w in topic_terms for w in words_in_query):
                has_body_region = True
                print("[ABSTENTION GATE-FALLBACK] Bypassed anatomical check because query matches a known guideline topic keyword.")
        except Exception as e:
            print(f"[WARN] Error running abstention gate fallback: {e}")

    has_condition = False
    if fhir_bundle:
        for entry in fhir_bundle.get("entry", []):
            resource = entry.get("resource", {})
            res_type = resource.get("resourceType")
            if res_type == "Condition":
                has_condition = True
                break

    if not has_body_region and not has_condition:
        return True, "Insufficient clinical detail: No identifiable anatomical region, body part, or clinical condition was specified."

    return False, ""


def sanitize_rag_input(text: str) -> str:
    """
    Strips prompt injection patterns from clinical text before it reaches the RAG pipeline.
    """
    if not text:
        return ""
    
    cleaned = text
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            print(f"[WARN] Prompt injection pattern matched in RAG input: {pattern.pattern}. Stripping.")
            cleaned = pattern.sub("", cleaned)
            
    return cleaned[:4000]


def query_acr_guidelines(clinical_scenario: str, fhir_bundle: dict = None) -> dict:
    """
    Executes the PEA prompt against the vector database for a given clinical scenario.
    Returns the generated response and the source documents.
    Uses SQLite database cache for identical queries to bypass model inference.
    """
    init_rag()
    
    # Redact PHI from input scenario before database lookup or logging
    redacted_scenario = redact_phi(clinical_scenario)
    
    # Sanitize RAG input to strip prompt injection patterns
    sanitized_scenario = sanitize_rag_input(redacted_scenario)
    
    # Evaluate input completeness (abstention gate)
    should_abstain, reason = evaluate_input_completeness(sanitized_scenario, fhir_bundle)
    if should_abstain:
        print(f"[ABSTENTION GATE] Abstaining from RAG query. Reason: {reason}")
        return {
            "abstained": True,
            "abstention_reason": reason,
            "recommendation": f"I cannot answer this based on the provided ACR guidelines. (Reason: {reason})",
            "sources": [],
            "raw_query": redacted_scenario,
            "expanded_query": sanitized_scenario
        }
        
    # Check SQLite cache
    cached = get_cached_query(sanitized_scenario)
    if cached:
        print(f"[CACHE HIT] Scenario: '{sanitized_scenario}' (SQLite)")
        if isinstance(cached, dict):
            cached["abstained"] = False
            if "confidence_score" not in cached:
                scores = [src.get("metadata", {}).get("score", 1.0) for src in cached.get("sources", [])]
                cached["confidence_score"] = round(sum(scores[:3]) / len(scores[:3]), 4) if scores else 1.0
            return cached
        
    print(f"[CACHE MISS] Executing RAG query for scenario: '{sanitized_scenario}'")
    
    # 1. Apply local abbreviation expansion first
    locally_expanded = _expand_abbreviations_locally(sanitized_scenario)
    if locally_expanded != sanitized_scenario:
        print(f"[LOCAL-EXPANSION] Expanded query: '{locally_expanded}'")
    
    # 2. Expand query before calling retriever to resolve medical jargon (if enabled)
    if ENABLE_NLP_EXPANSION:
        expanded_scenario = _expand_clinical_query(locally_expanded)
        print(f"[NLP-EXPANSION] LLM Expanded query: '{expanded_scenario}'")
    else:
        print("[NLP-EXPANSION] Clinical NLP query expansion is disabled. Using raw/locally-expanded query.")
        expanded_scenario = locally_expanded
    
    docs = _retriever.invoke(expanded_scenario)
    context = format_docs(docs)
    
    response = _invoke_with_retry(_chain, {"context": context, "question": sanitized_scenario})
    
    # Compute confidence score as the average similarity score of the top-3 retrieved docs
    scores = [doc.metadata.get("score", 1.0) for doc in docs[:3]]
    confidence = sum(scores) / len(scores) if scores else 1.0
    
    result = {
        "recommendation": response,
        "sources": [{"content": d.page_content, "metadata": d.metadata} for d in docs],
        "raw_query": redacted_scenario,
        "expanded_query": expanded_scenario,
        "abstained": False,
        "confidence_score": round(confidence, 4)
    }
    
    # Save to SQLite cache
    set_cached_query(sanitized_scenario, result)
    return result
