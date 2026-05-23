"""
Multi-scenario validation test.
Tests 5 different clinical queries to ensure CombinedTypeRetriever
correctly retrieves table data across the full corpus.
"""
import re
import sys
sys.stdout.reconfigure(encoding='utf-8')

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker
import torch

# Import helpers from rag_engine
sys.path.insert(0, '.')
from rag_engine import CombinedTypeRetriever, _extract_scenario, _extract_topic

device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"Device: {device}\n")

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-en-v1.5",
    model_kwargs={'device': device},
    encode_kwargs={'normalize_embeddings': True},
)
db = Chroma(persist_directory="chroma_db", embedding_function=embeddings)
cross_encoder = HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-v2-m3", model_kwargs={'device': device})
reranker = CrossEncoderReranker(model=cross_encoder, top_n=3)

retriever = CombinedTypeRetriever(
    db=db, reranker=reranker, embeddings=embeddings,
    k_tables=20, k_scenario_tables=50, k_narrative=20, top_n_narrative=3
)

TEST_QUERIES = [
    "69 yo female low back pain with suspected cauda equina syndrome",
    "acute headache worst headache of life thunderclap",
    "breast cancer screening average risk woman 40 years old",
    "chest pain shortness of breath suspected pulmonary embolism",
    "child with suspected appendicitis abdominal pain fever",
]

print("=" * 70)
for query in TEST_QUERIES:
    print(f"\nQUERY: {query}")
    docs = retriever.invoke(query)
    tables = [d for d in docs if d.metadata.get('type') == 'variant_table']
    narratives = [d for d in docs if d.metadata.get('type') == 'narrative']
    
    print(f"  Tables: {len(tables)}  |  Narratives: {len(narratives)}")
    if tables:
        scenario = _extract_scenario(tables[0].page_content)
        topic = _extract_topic(tables[0].page_content)
        print(f"  Matched scenario: [{topic}] {scenario}")
        # Show appropriateness ratings
        for d in tables[:5]:
            proc_m = re.search(r"Procedure:\s*(.+?)(?:\n|Appropriateness)", d.page_content, re.IGNORECASE)
            cat_m = re.search(r"Appropriateness Category:\s*(.+?)(?:\n|Adult|$)", d.page_content, re.IGNORECASE)
            proc = proc_m.group(1).strip() if proc_m else "?"
            cat = cat_m.group(1).strip() if cat_m else "?"
            print(f"    • {proc[:55]:<55} | {cat}")
    print("-" * 70)
