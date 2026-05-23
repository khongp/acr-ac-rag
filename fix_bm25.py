import pickle
from langchain_community.retrievers import BM25Retriever

print("Loading chunks...")
with open("data/bm25_chunks.pkl", "rb") as f:
    chunks = pickle.load(f)

print("Building BM25 Retriever...")
retriever = BM25Retriever.from_documents(chunks)

print("Saving BM25 Retriever...")
with open("data/bm25_retriever.pkl", "wb") as f:
    pickle.dump(retriever, f)

print("Done!")
