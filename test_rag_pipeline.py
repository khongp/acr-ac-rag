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

import time

scenario = "78 yo male with acute onset of severe headache, worst headache of life, neck stiffness"
print("Warming up models by running first query...")
query_acr_guidelines(scenario)

scenario2 = "45yo female with sudden onset of chest pain, shortness of breath, elevated D-dimer"
print(f"\nQuerying RAG pipeline (cache miss) with: '{scenario2}'...\n")

t0 = time.time()
result = query_acr_guidelines(scenario2)
t1 = time.time()
print(f"\n[LATENCY] Optimized warmed-up cache-miss RAG query took: {t1 - t0:.2f} seconds\n")

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
