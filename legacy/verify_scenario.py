"""Verify scenario extraction from DB."""
import re
import os
from dotenv import load_dotenv
load_dotenv()

EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "local").strip().lower()

if EMBEDDING_MODE == "gemini":
    from ingest import CachedGoogleGenerativeAIEmbeddings
    embeddings = CachedGoogleGenerativeAIEmbeddings()
    CHROMA_PATH = "chroma_db_gemini"
else:
    from langchain_huggingface import HuggingFaceEmbeddings
    embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
    CHROMA_PATH = "chroma_db_local"

from langchain_chroma import Chroma
db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)

def _extract_scenario(content):
    m = re.search(r"Clinical Scenario \(Variant\):\s*(.+?)(?:\n|$|Procedure:)", content, re.IGNORECASE)
    return m.group(1).strip().lower() if m else ""

def _extract_topic(content):
    m = re.search(r"Topic:\s*(.+?)(?:\n|Clinical Scenario)", content, re.IGNORECASE)
    return m.group(1).strip() if m else ""

query = "69 yo female low back pain with suspected cauda equina"
probe = db.similarity_search(query, k=3, filter={"type": "variant_table"})

print("=== TOP TABLE DOCS ===")
for i, d in enumerate(probe):
    sc = _extract_scenario(d.page_content)
    tp = _extract_topic(d.page_content)
    print(f"[{i}] topic='{tp}', scenario='{sc}'")
    print(f"    content: {d.page_content[:200].replace(chr(10), ' ')}")

print("\n=== SEARCHING FOR ALL SCENARIO ROWS ===")
if probe:
    best = probe[0]
    scenario = _extract_scenario(best.page_content)
    topic = _extract_topic(best.page_content)
    print(f"Filtering by topic='{topic}' and scenario='{scenario}'")
    
    all_topic = db.similarity_search(
        query, k=20,
        filter={"$and": [{"type": {"$eq": "variant_table"}}, {"topic": {"$eq": topic}}]}
    )
    matched = [d for d in all_topic if _extract_scenario(d.page_content) == scenario]
    
    print(f"\nMatched {len(matched)} rows for this scenario:")
    for d in matched:
        proc = re.search(r"Procedure:\s*(.+?)(?:\n|Appropriateness)", d.page_content, re.IGNORECASE)
        cat = re.search(r"Appropriateness Category:\s*(.+?)(?:\n|Adult|Pediatric|$)", d.page_content, re.IGNORECASE)
        print(f"  - {proc.group(1).strip() if proc else 'N/A'} | {cat.group(1).strip() if cat else 'N/A'}")
