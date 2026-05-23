from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain_classic.retrievers.document_compressors import CrossEncoderReranker

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-en-v1.5",
    model_kwargs={'device': 'cpu'},
    encode_kwargs={'normalize_embeddings': True},
)
db = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

query = "low back pain with suspected cauda equina"

# Retrieve 10 of each type
table_docs = db.similarity_search(query, k=10, filter={"type": "variant_table"})
narrative_docs = db.similarity_search(query, k=10, filter={"type": "narrative"})

candidate_docs = table_docs + narrative_docs
print(f"Total candidates: {len(candidate_docs)}")

# Initialize reranker
cross_encoder = HuggingFaceCrossEncoder(
    model_name="BAAI/bge-reranker-v2-m3",
    model_kwargs={'device': 'cpu'}
)
reranker = CrossEncoderReranker(model=cross_encoder, top_n=5)

# Compress/Rerank
reranked_docs = reranker.compress_documents(candidate_docs, query)

print("\n--- Top 5 Reranked Docs ---")
for i, d in enumerate(reranked_docs):
    t = d.metadata.get('type')
    print(f"[{i}] Type: {t}, Topic/Source: {d.metadata.get('topic') or d.metadata.get('source')}")
    print(f"    Metadata: {d.metadata}")
    print(f"    Snippet: {d.page_content[:200].replace(chr(10), ' ')}")
