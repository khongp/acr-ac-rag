from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-large-en-v1.5",
    model_kwargs={'device': 'cpu'},
    encode_kwargs={'normalize_embeddings': True},
)
db = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

query = "low back pain with suspected cauda equina"
print(f"Querying: {query}")
docs = db.similarity_search(query, k=20)
for i, d in enumerate(docs):
    t = d.metadata.get('type')
    print(f"[{i}] Type: {t}, Source: {d.metadata.get('source') or d.metadata.get('topic')}")
    print(f"    Snippet: {d.page_content[:150].replace(chr(10), ' ')}")

print("\n--- Doing search for 'cauda equina' ---")
docs = db.similarity_search("cauda equina", k=20)
for i, d in enumerate(docs):
    t = d.metadata.get('type')
    print(f"[{i}] Type: {t}, Source: {d.metadata.get('source') or d.metadata.get('topic')}")
    print(f"    Snippet: {d.page_content[:150].replace(chr(10), ' ')}")
