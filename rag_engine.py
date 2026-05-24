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


def init_cache_db():
    """Ensure query cache and overrides tables exist in SQLite database."""
    os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(CACHE_DB_PATH)
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
        conn = sqlite3.connect(CACHE_DB_PATH)
        cursor = conn.cursor()
        ts = datetime.now().isoformat()
        cursor.execute("""
            INSERT INTO clinician_overrides (timestamp, query_key, original_recommendation, overridden_recommendation, override_reason, clinician_notes)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (ts, query_key.strip().lower(), original, overridden, reason, notes))
        conn.commit()
        conn.close()
        print(f"[OVERRIDE] Saved override for query '{query_key}' (Reason: {reason})")
    except Exception as e:
        print(f"[WARN] Error saving clinician override: {e}")


def get_clinician_overrides() -> list:
    """Retrieve the audit history of clinician overrides from SQLite."""
    try:
        conn = sqlite3.connect(CACHE_DB_PATH)
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
    cache_key = clinical_scenario.strip().lower()
    try:
        conn = sqlite3.connect(CACHE_DB_PATH)
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
    cache_key = clinical_scenario.strip().lower()
    try:
        conn = sqlite3.connect(CACHE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO query_cache (query_key, recommendation, sources) VALUES (?, ?, ?)",
            (cache_key, result["recommendation"], json.dumps(result["sources"]))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[WARN] Error writing cache: {e}")



def init_procedures_db():
    """Ensure the acr_procedures table exists and is populated in SQLite."""
    os.makedirs(os.path.dirname(PROCEDURES_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(PROCEDURES_DB_PATH)
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
        conn = sqlite3.connect(PROCEDURES_DB_PATH)
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
    Returns the top 3 most clinically relevant candidate scenarios.
    """
    if not candidates:
        return []
    if len(candidates) <= 3:
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
            return candidates[:3]
            
        return selected[:3]
        
    except Exception as e:
        print(f"[WARN] LLM reranker failed: {e}. Falling back to default vector/BM25 rank.")
        return candidates[:3]


class CombinedTypeRetriever(BaseRetriever):
    db: Any
    embeddings: Any
    bm25_retriever: Any = None
    k_tables: int = 30
    k_narrative: int = 5

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        # ── Step 1: Query embedding ──
        query_emb = self.embeddings.embed_query(query)

        # ── Step 2: Probe table docs for scenario detection (Hybrid Search) ──
        vector_tables = self.db.similarity_search_by_vector(
            query_emb, k=self.k_tables, filter={"type": "variant_table"}
        )
        
        bm25_tables = []
        if self.bm25_retriever:
            try:
                bm25_docs = self.bm25_retriever.invoke(query)
                bm25_tables = [d for d in bm25_docs if d.metadata.get("type") == "variant_table"]
            except Exception as e:
                print(f"[WARN] BM25 table query failed: {e}")
                
        # Reciprocal Rank Fusion
        fused_tables = reciprocal_rank_fusion([vector_tables, bm25_tables])

        # ── Step 3: Scenario detection ──
        unique_candidates = []
        for doc in fused_tables:
            sc = _extract_scenario(doc.page_content)
            tp = _extract_topic(doc.page_content)
            if sc and tp:
                pair = (tp, sc)
                if pair not in unique_candidates:
                    unique_candidates.append(pair)

        # Rerank candidates using LLM to choose the top 3
        print(f"[RERANK] Prompting LLM to rerank {len(unique_candidates)} unique candidates...")
        best_candidates = _rerank_scenarios_llm(query, unique_candidates)
        print(f"[RERANK] Selected top {len(best_candidates)} candidates.")
        
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
                        
                    scenario_tables.append(Document(
                        page_content=content,
                        metadata={
                            "source": "acr_variant_tables.json",
                            "type": "variant_table",
                            "topic": best_topic,
                            "scenario": best_scenario,
                        }
                    ))
        else:
            print("[WARN] No scenario matched in probe. Using vector probe documents directly.")
            scenario_tables = vector_tables[:5]

        # ── Step 5: Get narratives via hybrid search ──
        vector_narratives = self.db.similarity_search_by_vector(
            query_emb, k=self.k_narrative, filter={"type": "narrative"}
        )
        
        bm25_narratives = []
        if self.bm25_retriever:
            try:
                bm25_docs = self.bm25_retriever.invoke(query)
                bm25_narratives = [d for d in bm25_docs if d.metadata.get("type") == "narrative"]
            except Exception as e:
                print(f"[WARN] BM25 narrative query failed: {e}")
                
        fused_narratives = reciprocal_rank_fusion([vector_narratives, bm25_narratives])
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
        k_narrative=5,
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


def query_acr_guidelines(clinical_scenario: str) -> dict:
    """
    Executes the PEA prompt against the vector database for a given clinical scenario.
    Returns the generated response and the source documents.
    Uses SQLite database cache for identical queries to bypass model inference.
    """
    init_rag()
    
    # Check SQLite cache
    cached = get_cached_query(clinical_scenario)
    if cached:
        print(f"[CACHE HIT] Scenario: '{clinical_scenario}' (SQLite)")
        return cached
        
    print(f"[CACHE MISS] Executing RAG query for scenario: '{clinical_scenario}'")
    
    # Expand query before calling retriever to resolve medical jargon
    expanded_scenario = _expand_clinical_query(clinical_scenario)
    print(f"[NLP-EXPANSION] Expanded query: '{expanded_scenario}'")
    
    docs = _retriever.invoke(expanded_scenario)
    context = format_docs(docs)
    
    response = _chain.invoke({"context": context, "question": clinical_scenario})
    
    result = {
        "recommendation": response,
        "sources": [{"content": d.page_content, "metadata": d.metadata} for d in docs]
    }
    
    # Save to SQLite cache
    set_cached_query(clinical_scenario, result)
    return result
