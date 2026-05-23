from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-en-v1.5",
    model_kwargs={'device': 'cpu'},
    encode_kwargs={'normalize_embeddings': True},
)
db = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

# Let's search with a filter: type = 'variant_table'
print("--- Similarity search with filter: type = 'variant_table' ---")
docs = db.similarity_search("low back pain with suspected cauda equina", k=10, filter={"type": "variant_table"})
print(f"Found {len(docs)} matching table docs:")
for i, d in enumerate(docs):
    print(f"[{i}] Topic: {d.metadata.get('topic')}")
    print(f"    Content: {d.page_content[:200].replace(chr(10), ' ')}")

# Let's compute cosine similarity between the query and a narrative doc vs a table doc to see the scores
import torch
import numpy as np

query_text = "low back pain with suspected cauda equina"
query_emb = embeddings.embed_query(query_text)

print("\n--- Scores for top matching table docs ---")
for i, d in enumerate(docs[:5]):
    doc_emb = embeddings.embed_documents([d.page_content])[0]
    score = np.dot(query_emb, doc_emb)
    print(f"Table doc {i} score: {score:.4f}")

print("\n--- Scores for top matching narrative docs ---")
narrative_docs = db.similarity_search(query_text, k=5, filter={"type": "narrative"})
for i, d in enumerate(narrative_docs):
    doc_emb = embeddings.embed_documents([d.page_content])[0]
    score = np.dot(query_emb, doc_emb)
    print(f"Narrative doc {i} score: {score:.4f} - Content: {d.page_content[:150].replace(chr(10), ' ')}")
