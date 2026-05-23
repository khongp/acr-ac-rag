import os
import sys
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
from rag_engine import query_acr_guidelines

# Set device to CPU to avoid CUDA conflicts with running backend
os.environ["CUDA_VISIBLE_DEVICES"] = ""

# Ensure we have mock Gemini key if needed, but it should load from dotenv
from dotenv import load_dotenv
load_dotenv()

scenario = "69 yo female low back pain with suspected cauda equina syndrome, new onset urinary retention"
print(f"Querying RAG pipeline with: '{scenario}'...\n")

result = query_acr_guidelines(scenario)

print("="*60)
print("RECOMMENDATION:")
print("="*60)
print(result["recommendation"])
print("\n" + "="*60)
print("SOURCES:")
print("="*60)
for i, src in enumerate(result["sources"]):
    print(f"\n[{i}] Type: {src['metadata'].get('type')}, Topic/Source: {src['metadata'].get('topic') or src['metadata'].get('source')}")
    print(f"Content: {src['content'][:200].replace(chr(10), ' ')}")
