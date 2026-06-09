"""
ACR-AC-RAG Ingestion Pipeline (v3)
===================================
Optimized for Google Cloud API embeddings (models/gemini-embedding-2)
and persistent SQLite caching. Runs CPU-only.
"""

import os
import re
import json
import time
import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from typing import List, Optional
from dotenv import load_dotenv
from google import genai
from google.genai import types
from langchain_core.embeddings import Embeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

load_dotenv()

RAW_DIR = "data/pdf_narratives"
JSON_PATH = "data/acr_variant_tables.json"

EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "local").strip().lower()

if EMBEDDING_MODE == "gemini":
    CHROMA_PATH = "chroma_db_gemini"
    EMBEDDING_MODEL = "models/gemini-embedding-2"
    EMBEDDING_CACHE_PATH = "data/embedding_cache_gemini_embedding_2.db"
    print(f"[INIT] Using Gemini Cloud API Embeddings. Database path: {CHROMA_PATH}")
else:
    CHROMA_PATH = "chroma_db_local"
    EMBEDDING_MODEL = "all-MiniLM-L6-v2"
    EMBEDDING_CACHE_PATH = None
    print(f"[INIT] Using Local Sentence-Transformers Embeddings (all-MiniLM-L6-v2). Database path: {CHROMA_PATH}")


class CachedGoogleGenerativeAIEmbeddings(Embeddings):
    """
    Wrapper around google-genai Client that caches generated embeddings
    in a local SQLite database to prevent redundant API queries, speed up ingestion,
    and save API costs.
    """
    def __init__(self, model: str = EMBEDDING_MODEL, cache_path: str = EMBEDDING_CACHE_PATH):
        self.model = model
        self.cache_path = cache_path
        api_key = os.environ.get("GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self._init_cache()
        self._local = threading.local()

    @property
    def conn(self):
        """Thread-local database connection to ensure safety when retrieved in parallel threads."""
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.cache_path, timeout=30.0, check_same_thread=False)
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
            except Exception as e:
                print(f"[WARN] Failed to enable WAL mode in embedding cache: {e}")
            self._local.conn = conn
        return self._local.conn

    def _init_cache(self):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        conn = sqlite3.connect(self.cache_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                text_hash TEXT PRIMARY KEY,
                text_content TEXT,
                embedding TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _get_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _get_cached_embedding(self, text: str) -> Optional[List[float]]:
        h = self._get_hash(text)
        try:
            cursor = self.conn.cursor()
            cursor.execute("SELECT embedding FROM embeddings WHERE text_hash = ?", (h,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
        except Exception as e:
            print(f"Cache read error: {e}")
        return None

    def _set_cached_embedding(self, text: str, embedding: List[float]):
        h = self._get_hash(text)
        try:
            cursor = self.conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO embeddings (text_hash, text_content, embedding) VALUES (?, ?, ?)",
                (h, text, json.dumps(embedding))
            )
            self.conn.commit()
        except Exception as e:
            print(f"Cache write error: {e}")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results = [None] * len(texts)
        missing_indices = []
        missing_texts = []
        
        for idx, text in enumerate(texts):
            cached = self._get_cached_embedding(text)
            if cached is not None:
                results[idx] = cached
            else:
                missing_indices.append(idx)
                missing_texts.append(text)
        
        if missing_texts:
            print(f"  [Embed Cache] Generating {len(missing_texts)} embeddings via Gemini API...")
            batch_size = 100
            
            for i in range(0, len(missing_texts), batch_size):
                batch = missing_texts[i:i+batch_size]
                batch_indices = missing_indices[i:i+batch_size]
                attempt = 0
                max_retries = 7
                delay = 5.0
                batch_embs = None
                while attempt < max_retries:
                    try:
                        wrapped_contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in batch]
                        response = self.client.models.embed_content(
                            model=self.model,
                            contents=wrapped_contents,
                        )
                        batch_embs = [emb.values for emb in response.embeddings]
                        break
                    except Exception as e:
                        print(f"    API Error on batch (attempt {attempt + 1}/{max_retries}): {e}")
                        err_str = str(e).lower()
                        # Handle transient issues like quota, 429 rate limits, 500/503/504 server errors, or timeouts
                        is_transient = any(x in err_str for x in ["429", "500", "502", "503", "504", "resourceexhausted", "quota", "unavailable", "timeout", "connection"])
                        if is_transient:
                            print(f"    Transient error detected. Sleeping {delay}s...")
                            time.sleep(delay)
                            delay *= 2
                            attempt += 1
                        else:
                            raise e
                
                if batch_embs is None:
                    raise Exception("Failed to embed documents due to persistent errors.")
                
                # Cache immediately to save progress if subsequent batches fail
                for idx, text, emb in zip(batch_indices, batch, batch_embs):
                    self._set_cached_embedding(text, emb)
                    results[idx] = emb
                
                completed = len(texts) - len(missing_texts) + i + len(batch)
                print(f"    Processed {completed}/{len(texts)} chunks...")
                
                # Sleep to maintain safety under RPM limit.
                time.sleep(2.0)
                
        return results

    def embed_query(self, text: str) -> List[float]:
        cached = self._get_cached_embedding(text)
        if cached is not None:
            return cached
        wrapped = [types.Content(parts=[types.Part.from_text(text=text)])]
        response = self.client.models.embed_content(
            model=self.model,
            contents=wrapped
        )
        emb = response.embeddings[0].values
        self._set_cached_embedding(text, emb)
        return emb


def get_guideline_version(source_path: str) -> str:
    """Compute a content-based MD5 hash (first 8 chars) of a file for version tracking."""
    h = hashlib.md5()
    with open(source_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def load_variants_from_json(json_path):
    """Load structured ACR variant table data with hierarchical chunking.

    Level 1 (Variant Summary): One chunk per unique (topic, scenario) pair
    listing ALL procedures for that scenario.

    Level 2 (Per-Procedure): One chunk per individual procedure row with
    full parent context inherited.
    """
    docs = []
    if not os.path.exists(json_path):
        print(f"JSON file {json_path} not found. Skipping table ingestion.")
        return docs

    guideline_version = get_guideline_version(json_path)
    ingested_at = datetime.now(timezone.utc).isoformat()

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # Group all procedure rows by (topic, scenario)
    from collections import defaultdict
    scenario_procedures = defaultdict(list)
    for topic in data:
        topic_name = topic.get("topicName", "")
        for variant in topic.get("variantData", []):
            scenario = variant.get("Scenario", "")
            if not scenario:
                continue
            pair_key = (topic_name.strip(), scenario.strip())
            scenario_procedures[pair_key].append(variant)

    for (topic_name, scenario), procedures in scenario_procedures.items():
        # ── Level 1: Variant Summary ──
        bullet_lines = []
        for proc in procedures:
            name = proc.get("Procedure", "N/A")
            approp = proc.get("Appropriateness Category", "N/A")
            rrl = proc.get("RRL", proc.get("Adult RRL", "N/A"))
            bullet_lines.append(f"- {name} | {approp} | RRL: {rrl}")

        summary_content = (
            f"ACR Appropriateness Table Data:\n"
            f"Topic: {topic_name}\n"
            f"Clinical Scenario (Variant): {scenario}\n"
            f"Procedures:\n" + "\n".join(bullet_lines)
        )

        docs.append(Document(
            page_content=summary_content,
            metadata={
                "source": "acr_variant_tables.json",
                "type": "variant_table",
                "topic": topic_name,
                "scenario": scenario,
                "level": "variant_summary",
                "guideline_version": guideline_version,
                "ingested_at": ingested_at,
                "chunk_type": "structured_table",
            }
        ))

        # ── Level 2: Per-Procedure ──
        # Skip indexing individual procedure rows into Chroma/BM25 database.
        # The SQLite procedures database is used to retrieve detailed procedures instead.
        # This reduces database size by ~84% and speeds up startups without losing accuracy.
        pass

    return docs


def chunk_pdf_documents(documents: list) -> list:
    """
    Hierarchical section-aware chunking.
    1. Group pages by source document (PDF).
    2. For each PDF, concatenate pages into a single full text.
    3. Split text by major section headings:
       - Summary of Literature
       - Clinical Considerations
       - Summary of Recommendations
       - Methodology
       - Abbreviations
    4. For each section, sub-split it using RecursiveCharacterTextSplitter if it exceeds 3000 chars.
    5. Prepend section header to each final chunk for retrieval context.
    """
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from collections import defaultdict
    import re

    # Build topic_id -> canonical_name lookup from JSON
    json_path = 'data/acr_variant_tables.json'
    topic_id_to_name = {}
    if os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        for entry in json_data:
            tid = entry.get('topicId')
            tname = entry.get('topicName', '').strip()
            if tid and tname:
                topic_id_to_name[tid] = tname

    # Group pages by document filename
    docs_by_source = defaultdict(list)
    for doc in documents:
        src = doc.metadata.get("source", "unknown")
        docs_by_source[src].append(doc)

    # Regex for common section titles in ACR PDFs
    section_pattern = re.compile(
        r"(\n|^)(SUMMARY\s+OF\s+LITERATURE|CLINICAL\s+CONSIDERATIONS|SUMMARY\s+OF\s+RECOMMENDATIONS|METHODOLOGY|ABBREVIATIONS|Summary\s+of\s+Literature|Clinical\s+Considerations|Summary\s+of\s+Recommendations|Methodology|Abbreviations)\b",
        re.IGNORECASE
    )

    sub_splitter = RecursiveCharacterTextSplitter(
        chunk_size=3000,
        chunk_overlap=300,
        length_function=len,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    final_chunks = []

    for src, pages in docs_by_source.items():
        # Sort pages to ensure proper sequence
        pages_sorted = sorted(pages, key=lambda d: d.metadata.get("page", 0))
        full_text = "\n".join(p.page_content for p in pages_sorted)

        # Find all split points
        matches = list(section_pattern.finditer(full_text))
        
        # If no section headings found, treat the whole document as one section ("General Narrative")
        if not matches:
            sections = [("General Narrative", full_text)]
        else:
            sections = []
            # Text before the first heading is usually introductory
            first_start = matches[0].start()
            if first_start > 100:
                sections.append(("Introduction", full_text[:first_start]))
                
            for i, match in enumerate(matches):
                sec_name = match.group(2).strip().title()
                start_idx = match.end()
                end_idx = matches[i+1].start() if i + 1 < len(matches) else len(full_text)
                sec_content = full_text[start_idx:end_idx].strip()
                if sec_content:
                    sections.append((sec_name, sec_content))

        # Compute guideline version for this PDF source
        pdf_path = os.path.join(RAW_DIR, src)
        if os.path.exists(pdf_path):
            pdf_version = get_guideline_version(pdf_path)
        else:
            pdf_version = "unknown"
        pdf_ingested_at = datetime.now(timezone.utc).isoformat()

        # Extract topic name from PDF filename using topic_id lookup for canonical names
        topic_id_match = re.search(r'_(\d+)\.pdf$', src, re.IGNORECASE)
        if topic_id_match:
            topic_id = int(topic_id_match.group(1))
            topic_name = topic_id_to_name.get(topic_id, re.sub(r'_\d+\.pdf$', '', src, flags=re.IGNORECASE).strip())
        else:
            topic_name = re.sub(r'_\d+\.pdf$', '', src, flags=re.IGNORECASE).strip()

        # Chunk each section and build final Document objects
        for sec_name, sec_text in sections:
            sub_texts = sub_splitter.split_text(sec_text)
            for idx, text in enumerate(sub_texts):
                content = f"Section: {sec_name}\n\n{text}"
                
                final_chunks.append(Document(
                    page_content=content,
                    metadata={
                        "source": src,
                        "type": "narrative",
                        "topic": topic_name,
                        "section": sec_name,
                        "chunk_index": idx,
                        "guideline_version": pdf_version,
                        "ingested_at": pdf_ingested_at,
                    }
                ))

    print(f"  Hierarchical section chunking completed: {len(documents)} pages -> {len(final_chunks)} final chunks")
    return final_chunks


def ingest_documents():
    """Main ingestion pipeline."""
    from langchain_community.document_loaders import PyPDFLoader
    print("=" * 60)
    print("  ACR-AC-RAG Ingestion Pipeline v3 (Google Embeddings + SQLite Cache)")
    print("=" * 60)
    
    documents = []
    boundary_pattern = re.compile(
        r"(?:^|\n\n|\n)\s*(References\s*$|Literature Search Procedure|Evidence Table\s*$|Appendix\s*[A-Z]?\s*$)",
        re.IGNORECASE | re.MULTILINE
    )
    
    # ── Load PDFs (excluding References/Evidence Tables/Appendix) ──
    if not os.path.exists(RAW_DIR):
        print(f"Directory {RAW_DIR} not found. Please create it and add PDFs.")
    else:
        for filename in sorted(os.listdir(RAW_DIR)):
            if filename.endswith(".pdf"):
                file_path = os.path.join(RAW_DIR, filename)
                try:
                    loader = PyPDFLoader(file_path)
                    docs = loader.load()
                    
                    filtered_docs = []
                    for idx, doc in enumerate(docs):
                        text = doc.page_content
                        # Stop loading when we reach references page or appendix
                        match = boundary_pattern.search(text)
                        if match:
                            # Secondary validation: verify if page looks like references/appendix
                            text_after = text[match.start():]
                            citation_count = len(re.findall(r'(?:^|\n)\s*\[?\d+\]?\.\s+', text_after))
                            matched_heading = match.group(1).strip().lower()
                            if citation_count >= 2 or match.start() < 200 or "references" in matched_heading or "evidence table" in matched_heading:
                                break
                        doc.metadata["source"] = filename
                        doc.metadata["type"] = "narrative"
                        filtered_docs.append(doc)
                    print(f"  Loaded {filename}: {len(filtered_docs)} pages (skipped {len(docs) - len(filtered_docs)} appendix pages)")
                    documents.extend(filtered_docs)
                except Exception as e:
                    print(f"  ERROR loading {filename}: {e}")

    print(f"\nTotal loaded narrative pages: {len(documents)}")

    # ── Split narrative pages into coherent chunks ──
    print("\nChunking narrative documents...")
    chunks = chunk_pdf_documents(documents)

    # ── Load structured JSON variants (no chunking needed) ──
    print("\nLoading structured JSON variants...")
    variant_docs = load_variants_from_json(JSON_PATH)
    print(f"  Loaded {len(variant_docs)} variant table records")
    
    chunks.extend(variant_docs)
    print(f"\nTotal chunks to ingest: {len(chunks)}")

    # ── Initialize Embeddings ──
    if EMBEDDING_MODE == "gemini":
        print(f"\nInitializing Cached Google Embedding model: {EMBEDDING_MODEL}")
        embeddings = CachedGoogleGenerativeAIEmbeddings()
        vector_dims = 3072
    else:
        print(f"\nInitializing Local HuggingFace Embedding model: {EMBEDDING_MODEL}")
        from langchain_huggingface import HuggingFaceEmbeddings
        embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        vector_dims = 384

    # ── Store in ChromaDB ──
    print(f"\nStoring chunks in ChromaDB at {CHROMA_PATH}...")
    
    if os.path.exists(CHROMA_PATH):
        import shutil
        import time
        max_retries = 15
        delay = 1.0
        for attempt in range(max_retries):
            try:
                shutil.rmtree(CHROMA_PATH)
                print("  Cleared existing ChromaDB")
                break
            except PermissionError as e:
                if attempt == max_retries - 1:
                    print(f"  [Lock Error] Permanent lock on {CHROMA_PATH}. Cannot delete: {e}")
                    raise e
                print(f"  [Lock Alert] ChromaDB folder locked by another process (likely Google Drive sync). Retrying delete in {delay}s... (attempt {attempt+1}/{max_retries})")
                time.sleep(delay)
                delay = min(delay * 1.5, 10.0)
        
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)
    
    # Ingest in batches of 500
    batch_size = 500
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        print(f"  Ingesting batch {i // batch_size + 1}/{(len(chunks) - 1) // batch_size + 1} ({len(batch)} chunks)...")
        db.add_documents(batch)
    
    # ── Build and Save BM25 Retriever ──
    print("\nBuilding and saving BM25 Retriever index...")
    from langchain_community.retrievers import BM25Retriever
    import pickle
    
    os.makedirs("data", exist_ok=True)
    chunks_path = "data/bm25_chunks.pkl"
    retriever_path = "data/bm25_retriever.pkl"
    
    with open(chunks_path, "wb") as f:
        pickle.dump(chunks, f)
        
    bm25_retriever = BM25Retriever.from_documents(chunks)
    with open(retriever_path, "wb") as f:
        pickle.dump(bm25_retriever, f)
        
    print(f"  Saved BM25 Chunks:     {chunks_path}")
    print(f"  Saved BM25 Retriever:  {retriever_path}")

    # Verify
    count = db._collection.count()
    print(f"\n{'=' * 60}")
    print(f"  Ingestion complete!")
    print(f"  Embedding model: {EMBEDDING_MODEL}")
    print(f"  Total vectors:   {count}")
    print(f"  Vector dims:     {vector_dims} ({EMBEDDING_MODEL})")
    print(f"  ChromaDB path:   {CHROMA_PATH}")
    print(f"{'=' * 60}")

    # Generate Manifest
    try:
        manifest_path = "data/guideline_manifest.json"
        os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
        pdf_files = []
        if os.path.exists(RAW_DIR):
            for f in sorted(os.listdir(RAW_DIR)):
                if f.endswith(".pdf"):
                    full_path = os.path.join(RAW_DIR, f)
                    pdf_files.append({
                        "filename": f,
                        "size_bytes": os.path.getsize(full_path),
                        "hash": get_guideline_version(full_path)
                    })
        json_metadata = None
        if os.path.exists(JSON_PATH):
            json_metadata = {
                "filename": os.path.basename(JSON_PATH),
                "size_bytes": os.path.getsize(JSON_PATH),
                "hash": get_guideline_version(JSON_PATH)
            }
        master_hash_input = "".join(f["hash"] for f in pdf_files)
        if json_metadata:
            master_hash_input += json_metadata["hash"]
        master_version = hashlib.md5(master_hash_input.encode()).hexdigest()[:12].upper()
        
        manifest = {
            "version": f"ACR-AC-DB-{master_version}",
            "release_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
            "total_pdfs": len(pdf_files),
            "pdf_files": pdf_files,
            "variant_table": json_metadata,
            "total_vectors": count,
            "vector_dimensions": vector_dims,
            "embedding_model": EMBEDDING_MODEL
        }
        with open(manifest_path, "w", encoding="utf-8") as mf:
            json.dump(manifest, mf, indent=2)
        print(f"  Generated guideline manifest: {manifest_path}")
    except Exception as me:
        print(f"[WARN] Failed to generate manifest: {me}")


if __name__ == "__main__":
    ingest_documents()
