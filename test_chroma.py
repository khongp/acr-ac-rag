"""Test direct ChromaDB retrieval."""
import sys
sys.stdout.reconfigure(encoding='utf-8')
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

CHROMA_PATH = "chroma_db"
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"

import traceback
try:
    print("Loading embeddings...")
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={'device': device},
        encode_kwargs={'normalize_embeddings': True},
    )

    print("Loading DB...")
    db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)

    query = "low back pain with suspected cauda equina"
    print(f"\nQuerying: {query}")
    docs = db.similarity_search(query, k=20)

    table_count = 0
    for i, d in enumerate(docs):
        t = d.metadata.get('type')
        if t == 'table':
            table_count += 1
            print(f"[{i}] TABLE: {d.metadata.get('scenario')} -> {d.metadata.get('procedure')}")
        else:
            print(f"[{i}] NARRATIVE: {d.metadata.get('source')}")
    print(f"\nTotal tables found in top 20: {table_count}")
except Exception as e:
    traceback.print_exc()
