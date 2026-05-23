import os
import time
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

api_key = os.environ.get("GOOGLE_API_KEY")
client = genai.Client(api_key=api_key)

texts = ["Hello world from Gemini API test. We are testing batch sizes to optimize ChromaDB ingestion speed and reliability."] * 10

# Test 1: Using wrapped types
print(f"Embedding {len(texts)} texts in a single batch with types wrapper...")
t0 = time.time()
try:
    wrapped_contents = [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]
    response = client.models.embed_content(
        model="models/gemini-embedding-2",
        contents=wrapped_contents,
    )
    embeddings = response.embeddings
    t1 = time.time()
    print(f"Success! Embedded {len(embeddings)} texts in {t1 - t0:.2f} seconds.")
    print(f"Values count of first: {len(embeddings[0].values)}")
except Exception as e:
    print(f"Error: {e}")

# Test 2: Using raw list of strings (to see if the bug behaves as described)
print(f"\nEmbedding {len(texts)} texts in a single batch using raw list of strings...")
try:
    response = client.models.embed_content(
        model="models/gemini-embedding-2",
        contents=texts,
    )
    embeddings = response.embeddings
    print(f"Returned embeddings count: {len(embeddings)}")
    if embeddings:
        print(f"Values count of first: {len(embeddings[0].values)}")
except Exception as e:
    print(f"Error: {e}")

