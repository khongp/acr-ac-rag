from langchain_community.vectorstores import Chroma
from langchain_community.embeddings import HuggingFaceEmbeddings

CHROMA_PATH = "chroma_db"
embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
db = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)

query = "what imaging for increase dyspnea and right arm swelling"
print(f"Query: {query}\n")

# Try retrieving more chunks
docs = db.similarity_search(query, k=10)

for i, doc in enumerate(docs):
    print(f"--- Chunk {i+1} ---")
    print(f"Source: {doc.metadata.get('source')}")
    print(doc.page_content[:200] + "...\n")
