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


def init_cache_db():
    """Ensure query cache table exists in SQLite database."""
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
    conn.commit()
    conn.close()


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


def _choose_best_scenario_llm(query: str, candidates: List[tuple]) -> Optional[tuple]:
    """
    Call Gemini to select the single best matching topic/scenario pair from candidates.
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
        
    try:
        llm = ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.0)
        
        # Format candidates list
        candidates_text = ""
        for idx, (topic, scenario) in enumerate(candidates):
            candidates_text += f"{idx + 1}. Topic: \"{topic}\" | Scenario: \"{scenario}\"\n"
            
        prompt = f"""You are a clinical decision support assistant.
Your task is to select the single most clinically appropriate ACR variant scenario for the patient's presentation.

Patient Case:
{query}

ACR Variant Candidates:
{candidates_text}

Respond with ONLY the number of the best matching candidate (e.g. "1" or "2"). Do not include any other text, reasoning, or markdown. If none of the candidates are a good match, respond with "0"."""

        response = llm.invoke(prompt)
        content = response.content
        if isinstance(content, list):
            res_text = "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            ).strip()
        else:
            res_text = str(content).strip()
        # Parse index
        import re
        match = re.search(r"\d+", res_text)
        if match:
            idx = int(match.group(0)) - 1
            if 0 <= idx < len(candidates):
                return candidates[idx]
    except Exception as e:
        print(f"[WARN] LLM scenario routing failed: {e}")
        
    return None


class CombinedTypeRetriever(BaseRetriever):
    db: Any
    embeddings: Any
    k_tables: int = 30
    k_narrative: int = 5

    class Config:
        arbitrary_types_allowed = True

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        # ── Step 1: Query embedding ──
        query_emb = self.embeddings.embed_query(query)

        # ── Step 2: Probe table docs for scenario detection ──
        probe_tables = self.db.similarity_search_by_vector(
            query_emb, k=self.k_tables, filter={"type": "variant_table"}
        )

        # ── Step 3: Scenario detection ──
        unique_candidates = []
        for doc in probe_tables:
            sc = _extract_scenario(doc.page_content)
            tp = _extract_topic(doc.page_content)
            if sc and tp:
                pair = (tp, sc)
                if pair not in unique_candidates:
                    unique_candidates.append(pair)

        best_topic, best_scenario = None, None
        if unique_candidates:
            # Try LLM scenario router
            print(f"[ROUTER] Prompting LLM to choose from {len(unique_candidates)} candidate scenarios...")
            chosen = _choose_best_scenario_llm(query, unique_candidates)
            if chosen:
                best_topic, best_scenario = chosen
                print(f"[MATCH-LLM] Topic: '{best_topic}' | Scenario: '{best_scenario}'")
            else:
                # Fallback to majority vote
                pair_counts = Counter()
                for doc in probe_tables:
                    sc = _extract_scenario(doc.page_content)
                    tp = _extract_topic(doc.page_content)
                    if sc and tp:
                        pair_counts[(tp, sc)] += 1
                if pair_counts:
                    best_topic, best_scenario = pair_counts.most_common(1)[0][0]
                    print(f"[MATCH-VOTE] Topic: '{best_topic}' | Scenario: '{best_scenario}'")

        scenario_tables = []
        if best_topic and best_scenario:
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
            print("[WARN] No scenario matched in probe. Using probe documents directly.")
            scenario_tables = probe_tables

        # ── Step 5: Get narratives directly (no reranker model needed) ──
        narrative_candidates = self.db.similarity_search_by_vector(
            query_emb, k=self.k_narrative, filter={"type": "narrative"}
        )

        return scenario_tables + narrative_candidates


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
    global _retriever, _llm, _chain
    init_cache_db()
    init_procedures_db()
    if _retriever is None:
        _retriever = get_retriever()
    if _llm is None:
        _llm = get_llm()
    if _chain is None:
        _chain = prompt | _llm | StrOutputParser()


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
    docs = _retriever.invoke(clinical_scenario)
    context = format_docs(docs)
    
    response = _chain.invoke({"context": context, "question": clinical_scenario})
    
    result = {
        "recommendation": response,
        "sources": [{"content": d.page_content, "metadata": d.metadata} for d in docs]
    }
    
    # Save to SQLite cache
    set_cached_query(clinical_scenario, result)
    return result
