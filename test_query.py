from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

import traceback
import sys

import traceback

def test_rag():
    try:
        print("Loading embeddings...")
        import torch
        device = "cpu"
        embeddings = HuggingFaceEmbeddings(
            model_name="BAAI/bge-large-en-v1.5",
            model_kwargs={'device': device},
            encode_kwargs={'normalize_embeddings': True},
        )

        print("Loading DB...")
        db = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

        query = "low back pain with suspected cauda equina"
        print(f"\nQuerying: {query}")
        docs = db.similarity_search(query, k=20)

        table_count = 0
        for i, d in enumerate(docs):
            t = d.metadata.get('type')
            if t == 'variant_table':
                table_count += 1
                print(f"[{i}] TABLE: {d.metadata.get('topic')}")
                print(f"   {d.page_content.split('Procedure: ')[-1].split(chr(10))[0]}")
            else:
                print(f"[{i}] NARRATIVE: {d.metadata.get('source')}")
        print(f"\nTotal tables found in top 20: {table_count}")
    except Exception as e:
        traceback.print_exc()

if __name__ == "__main__":
    test_rag()
