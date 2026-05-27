"""
ACR-AC-RAG Retrieval Engine (v3)
=================================
Optimized for Google Cloud API embeddings (models/gemini-embedding-2)
and persistent SQLite caching. Runs CPU-only.
"""

import os
import re
import json
import sqlite3
import pickle
import time
from collections import Counter
from typing import List, Any, Optional

from langchain_chroma import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from dotenv import load_dotenv
from ingest import CachedGoogleGenerativeAIEmbeddings

load_dotenv()

EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "local").strip().lower()
ENABLE_NLP_EXPANSION = os.getenv("ENABLE_NLP_EXPANSION", "false").strip().lower() == "true"
ENABLE_LLM_RERANK = os.getenv("ENABLE_LLM_RERANK", "false").strip().lower() == "true"
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



def redact_phi(text: str) -> str:
    """
    Scans and redacts common patient identifiers (MRNs, SSNs, DOBs, phone numbers) 
    from clinical texts to ensure HIPAA compliance before writing to cache or logs.
    """
    if not text:
        return ""
    
    # 1. Phone numbers
    phone_pattern = r"\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
    text = re.sub(phone_pattern, "[REDACTED_PHONE]", text)
    
    # 2. SSN
    ssn_pattern = r"\b\d{3}-\d{2}-\d{4}\b"
    text = re.sub(ssn_pattern, "[REDACTED_SSN]", text)
    
    # 3. DOB
    dob_pattern = r"\b(?:dob|birthdate|birth\s+date)\s*[:-]?\s*(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{4}[-/]\d{1,2}[-/]\d{1,2})\b"
    text = re.sub(dob_pattern, "DOB: [REDACTED_DOB]", text, flags=re.IGNORECASE)
    
    # 4. MRN
    mrn_pattern = r"\b(?:mrn|medical\s+record\s+number)\s*[:-]?\s*\d{4,12}\b"
    text = re.sub(mrn_pattern, "MRN: [REDACTED_MRN]", text, flags=re.IGNORECASE)
    
    # 5. Names: Common clinical patterns like "patient John Doe", "Mr. Smith"
    name_patterns = [
        (r"\bpatient\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b", "patient [REDACTED_NAME]"),
        (r"\b(?:mr|ms|mrs|dr)\.\s*([A-Z][a-z]+)\b", "[REDACTED_TITLE] [REDACTED_LASTNAME]"),
    ]
    for pattern, repl in name_patterns:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
        
    return text


def get_db_connection(db_path: str) -> sqlite3.Connection:
    """
    Establish a SQLite database connection with a 30-second timeout 
    and WAL (Write-Ahead Logging) enabled for concurrency support.
    """
    conn = sqlite3.connect(db_path, timeout=30.0)
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
            sources TEXT
        )
    """)
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
    """Retrieve RAG response from cache if exists."""
    cache_key = redact_phi(clinical_scenario).strip().lower()
    try:
        conn = get_db_connection(CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT recommendation, sources FROM query_cache WHERE query_key = ?", (cache_key,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {
                "recommendation": row[0],
                "sources": json.loads(row[1])
            }
    except Exception as e:
        print(f"[WARN] Error reading cache: {e}")
    return None


def set_cached_query(clinical_scenario: str, result: dict):
    """Write RAG response to SQLite persistent cache."""
    cache_key = redact_phi(clinical_scenario).strip().lower()
    try:
        conn = get_db_connection(CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO query_cache (query_key, recommendation, sources) VALUES (?, ?, ?)",
            (cache_key, result["recommendation"], json.dumps(result["sources"]))
        )
        conn.commit()
        conn.close()
        sync_cache_to_gcs()
    except Exception as e:
        print(f"[WARN] Error writing cache: {e}")


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
    # Check if table already has rows
    cursor.execute("SELECT COUNT(*) FROM acr_procedures")
    count = cursor.fetchone()[0]
    
    if count == 0:
        json_path = "data/acr_variant_tables.json"
        if os.path.exists(json_path):
            print(f"[INIT] Populating SQLite procedures database from {json_path}...")
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
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
        from langchain_google_genai import ChatGoogleGenerativeAI
        from pydantic import BaseModel, Field
        from typing import List
        
        class SelectedCandidate(BaseModel):
            topic: str = Field(description="The exact Topic name from the candidate list")
            scenario: str = Field(description="The exact Scenario (Variant) name from the candidate list")
            rationale: str = Field(description="1-sentence explanation of why this fits the clinical presentation")
            
        class RerankedOutput(BaseModel):
            rankings: List[SelectedCandidate] = Field(description="Top 3 selected candidate scenarios, in order of clinical relevance")
            
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
        structured_llm = llm.with_structured_output(RerankedOutput)
        
        candidates_str = ""
        for idx, (t, s) in enumerate(candidates):
            candidates_str += f"{idx+1}. Topic: '{t}' | Scenario: '{s}'\n"
            
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
        
        res = structured_llm.invoke(prompt)
        
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


class CombinedTypeRetriever(BaseRetriever):
    db: Any
    embeddings: Any
    bm25_retriever: Any = None
    k_tables: int = 30
    k_narrative: int = 3

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        # ── Step 1: Query embedding ──
        query_emb = self.embeddings.embed_query(query)

        # ── Step 2: Probe table docs for scenario detection (Hybrid Search) ──
        try:
            vector_tables_with_scores = self.db.similarity_search_with_score(
                query, k=self.k_tables, filter={"type": "variant_table"}
            )
            vector_tables = []
            for doc, score in vector_tables_with_scores:
                doc.metadata["score"] = float(score)
                doc.metadata["retrieval_method"] = "vector"
                vector_tables.append(doc)
        except Exception as e:
            print(f"[WARN] Vector table search with score failed: {e}. Falling back to standard search.")
            vector_tables = self.db.similarity_search_by_vector(
                query_emb, k=self.k_tables, filter={"type": "variant_table"}
            )
            for doc in vector_tables:
                doc.metadata["score"] = 1.0
                doc.metadata["retrieval_method"] = "vector"
        
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

        # Rerank candidates using LLM to choose the top 3 (if enabled)
        if ENABLE_LLM_RERANK:
            print(f"[RERANK] Prompting LLM to rerank {len(unique_candidates)} unique candidates...")
            best_candidates = _rerank_scenarios_llm(query, unique_candidates)
            print(f"[RERANK] Selected top {len(best_candidates)} candidates.")
        else:
            print(f"[RERANK] LLM reranking is disabled. Using top {min(MAX_CANDIDATES, len(unique_candidates))} candidates from hybrid search.")
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
                    
                    content = f"ACR Appropriateness Table Data:\n"
                    content += f"Topic: {best_topic}\n"
                    content += f"Clinical Scenario (Variant): {best_scenario}\n"
                    content += f"Procedure: {proc}\n"
                    content += f"Appropriateness Category: {cat}\n"
                    if adult_rrl:
                        content += f"Adult Radiation Dose (RRL): {adult_rrl}\n"
                    if peds_rrl:
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

        # ── Step 5: Get narratives via hybrid search ──
        try:
            vector_narratives_with_scores = self.db.similarity_search_with_score(
                query, k=self.k_narrative, filter={"type": "narrative"}
            )
            vector_narratives = []
            for doc, score in vector_narratives_with_scores:
                doc.metadata["score"] = float(score)
                doc.metadata["retrieval_method"] = "vector"
                vector_narratives.append(doc)
        except Exception as e:
            print(f"[WARN] Vector narrative search with score failed: {e}. Falling back to standard search.")
            vector_narratives = self.db.similarity_search_by_vector(
                query_emb, k=self.k_narrative, filter={"type": "narrative"}
            )
            for doc in vector_narratives:
                doc.metadata["score"] = 1.0
                doc.metadata["retrieval_method"] = "vector"
        
        bm25_narratives = []
        if self.bm25_retriever:
            try:
                bm25_docs = self.bm25_retriever.invoke(query)
                bm25_narratives = [d for d in bm25_docs if d.metadata.get("type") == "narrative"]
                for doc in bm25_narratives:
                    doc.metadata["retrieval_method"] = "bm25"
                    doc.metadata["score"] = doc.metadata.get("score", 1.0)
            except Exception as e:
                print(f"[WARN] BM25 narrative query failed: {e}")
                
        fused_narratives = reciprocal_rank_fusion([vector_narratives, bm25_narratives])
        if ENABLE_COLBERT_RERANK:
            best_narratives = rerank_with_colbert(query, fused_narratives, top_k=self.k_narrative)
        else:
            best_narratives = fused_narratives[:self.k_narrative]

        return scenario_tables + best_narratives


def get_retriever():
    """Build the retrieval pipeline using the selected embedding model and ChromaDB."""
    if EMBEDDING_MODE == "gemini":
        print("Loading Google Gemini Embeddings (models/gemini-embedding-2) with cache...")
        embeddings = CachedGoogleGenerativeAIEmbeddings()
    else:
        print("Loading Local HuggingFace Embeddings (all-MiniLM-L6-v2)...")
        from langchain_huggingface import HuggingFaceEmbeddings
        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        
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
    if "GOOGLE_API_KEY" not in os.environ:
        raise ValueError("Please set the GOOGLE_API_KEY environment variable to use the LLM.")
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)


# PEA architecture prompt
template = """
You are an AI assistant designed exclusively to retrieve and summarize the published American College of Radiology (ACR) Appropriateness Criteria.
Please do not provide specific medical advice, independent diagnoses, or prescriptive treatment plans.
You must guide the user toward consulting with a licensed healthcare professional.
You must offer general, non-prescriptive information grounded ONLY in the provided text context.

IMPORTANT: 
1. If the context contains 'ACR Appropriateness Table Data' that matches the user's clinical presentation, strictly list the imaging modalities that are "Usually appropriate" (Ratings 7-9) followed by "May be appropriate" (Ratings 4-6). Always include the Radiation Dose (RRL).
2. If the context DOES NOT contain the exact table data, but DOES contain narrative text or guidelines relevant to the clinical scenario, you MUST summarize that narrative guidance. Do not simply refuse to answer.
3. Then, use any narrative text provided to add a brief 'Clinical Rationale / FYI' section explaining why.
4. Keep the output extremely concise and direct. Avoid verbose explanations or conversational filler. Be as brief as possible while providing the required information.

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
_chain = None


def init_rag():
    """Initialize all RAG components (embedding model, vector store, LLM).
    Safe to call multiple times — uses singleton pattern."""
    global _retriever, _llm, _chain, _bm25_retriever
    
    if _retriever is not None and _llm is not None and _chain is not None:
        return
    
    # 1. Copy database files from GCS source mounts to fast local /tmp storage on Cloud Run
    if CHROMA_SOURCE_PATH and os.path.exists(CHROMA_SOURCE_PATH):
        if not os.path.exists(CHROMA_PATH):
            print(f"[STARTUP] Copying ChromaDB from GCS mount {CHROMA_SOURCE_PATH} to local /tmp storage {CHROMA_PATH}...")
            import shutil
            shutil.copytree(CHROMA_SOURCE_PATH, CHROMA_PATH)
            print("[STARTUP] ChromaDB copy completed.")
            
    if PROCEDURES_SOURCE_PATH and os.path.exists(PROCEDURES_SOURCE_PATH):
        if not os.path.exists(PROCEDURES_DB_PATH):
            print(f"[STARTUP] Copying procedures DB from {PROCEDURES_SOURCE_PATH} to {PROCEDURES_DB_PATH}...")
            os.makedirs(os.path.dirname(PROCEDURES_DB_PATH), exist_ok=True)
            import shutil
            shutil.copy2(PROCEDURES_SOURCE_PATH, PROCEDURES_DB_PATH)
            print("[STARTUP] Procedures DB copy completed.")

    if CACHE_SOURCE_PATH and os.path.exists(CACHE_SOURCE_PATH):
        if not os.path.exists(CACHE_DB_PATH):
            print(f"[STARTUP] Copying query cache DB from {CACHE_SOURCE_PATH} to {CACHE_DB_PATH}...")
            os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
            import shutil
            shutil.copy2(CACHE_SOURCE_PATH, CACHE_DB_PATH)
            print("[STARTUP] Query cache DB copy completed.")

    init_cache_db()
    init_procedures_db()
    if _bm25_retriever is None:
        _bm25_retriever = load_bm25_retriever()
    if _retriever is None:
        _retriever = get_retriever()
    if _llm is None:
        _llm = get_llm()
    if _chain is None:
        _chain = prompt | _llm | StrOutputParser()


def _expand_clinical_query(query: str) -> str:
    """
    Uses Gemini to expand medical abbreviations, shorthand, and acronyms in the query.
    This is extremely cheap and low-latency, and makes keyword and vector RAG matching
    highly robust for real-world clinician inputs.
    """
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.0)
        prompt = (
            "You are a medical vocabulary expansion agent.\n"
            "Analyze the following clinical query and expand any shorthand, abbreviations, or acronyms "
            "to their full, formal medical names (e.g. resolve LBP to Low Back Pain, MVC to Motor Vehicle Collision, "
            "CES to cauda equina syndrome, GU to genitourinary, r/o to rule out, etc.).\n"
            "Return the original query text combined with the expanded terms as a single query string for a search engine.\n"
            "Keep the output extremely concise and return ONLY the resulting query string.\n\n"
            f"Clinical Query: {query}\n"
            "Expanded Search Query:"
        )
        res = llm.invoke(prompt)
        expanded = res.content.strip()
        return expanded if expanded else query
    except Exception as e:
        print(f"[WARN] Clinical NLP expansion failed: {e}. Using raw query.")
        return query


# ── RAG Input Sanitization and Completeness (Abstention Gate) ──

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(previous|all|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(your|all|the)\s+(system|previous|prior)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"new\s+instruction", re.IGNORECASE),
    re.compile(r"forget\s+(everything|all|your\s+instructions)", re.IGNORECASE),
    re.compile(r"act\s+as\s+(a\s+|an\s+)?(different|new|unrestricted)", re.IGNORECASE),
    re.compile(r"<\s*(script|iframe|object|embed)", re.IGNORECASE),
]

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
    
    # Expand query before calling retriever to resolve medical jargon (if enabled)
    if ENABLE_NLP_EXPANSION:
        expanded_scenario = _expand_clinical_query(sanitized_scenario)
        print(f"[NLP-EXPANSION] Expanded query: '{expanded_scenario}'")
    else:
        print("[NLP-EXPANSION] Clinical NLP query expansion is disabled. Using raw redacted query.")
        expanded_scenario = sanitized_scenario
    
    docs = _retriever.invoke(expanded_scenario)
    context = format_docs(docs)
    
    response = _chain.invoke({"context": context, "question": sanitized_scenario})
    
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
