import os
import sys
import sqlite3
import random
import argparse
from datetime import datetime
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
load_dotenv()

# UTF-8 for console output on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

from fhir_converter import convert_text_to_fhir_bundle, extract_scenario_from_bundle
from rag_engine import get_retriever, _extract_scenario, _extract_topic, init_rag
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Core topics to evaluate (diverse and highly relevant clinical scenarios)
TARGET_TOPICS = [
    "Major Blunt Trauma",
    "Head Trauma",
    "Low Back Pain",
    "Acute Hip Pain",
    "Rib Fractures",
    "Acute Spinal Trauma",
    "Suspected Spine Infection",
    "Renovascular Hypertension",
    "Jaundice",
    "Sinusitis",
    "Dyspnea",
    "Acute Neck Pain",
    "Hematuria",
    "Dementia",
    "Seizures"
]

def get_llm():
    if "GOOGLE_API_KEY" not in os.environ:
        raise ValueError("GOOGLE_API_KEY not found in environment.")
    return ChatGoogleGenerativeAI(model="gemini-3.5-flash", temperature=0.5)

def generate_synthetic_query(topic: str, scenario: str) -> str:
    """Generate a realistic, short clinical query from formal ACR descriptions."""
    llm = get_llm()
    template = (
        "You are a clinical simulator. Translate the following formal ACR Appropriateness Criteria scenario "
        "into a realistic, concise (1-2 sentences) query that a doctor would enter into an EHR or decision support tool.\n"
        "Include typical demographics (e.g. '67yo female', '25-year-old man') and clinical presentation/symptoms "
        "implied by the scenario, but do not use the exact wording of the scenario itself. Make it sound natural.\n\n"
        "ACR Topic: {topic}\n"
        "ACR Scenario: {scenario}\n\n"
        "Simulated Clinical Query:"
    )
    prompt = PromptTemplate.from_template(template)
    chain = prompt | llm | StrOutputParser()
    return chain.invoke({"topic": topic, "scenario": scenario}).strip()

def run_validation(sample_size: int = 15):
    print("=" * 70)
    print("   ACR-AC-RAG Batch Validation & Synthetic Query Generator")
    print("=" * 70)
    
    init_rag()
    retriever = get_retriever()
    
    # 1. Fetch scenarios for our target topics
    db_path = "data/acr_procedures.db"
    if not os.path.exists(db_path):
        print(f"Error: Procedures DB not found at {db_path}.")
        return
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    test_cases = []
    for topic in TARGET_TOPICS[:sample_size]:
        cursor.execute(
            "SELECT DISTINCT scenario_key FROM acr_procedures WHERE topic_key = ? ORDER BY RANDOM() LIMIT 1",
            (topic.lower(),)
        )
        row = cursor.fetchone()
        if row:
            # Get the original proper-case topic name and scenario name
            cursor.execute(
                "SELECT variant_json FROM acr_procedures WHERE topic_key = ? AND scenario_key = ? LIMIT 1",
                (topic.lower(), row[0])
            )
            val_row = cursor.fetchone()
            if val_row:
                import json
                var = json.loads(val_row[0])
                test_cases.append({
                    "topic": topic,
                    "scenario": var.get("Scenario", row[0])
                })
    
    conn.close()
    print(f"Selected {len(test_cases)} validation cases from SQLite.")
    
    passed_count = 0
    results = []
    
    for idx, case in enumerate(test_cases):
        topic = case["topic"]
        scenario = case["scenario"]
        print(f"\n[{idx+1}/{len(test_cases)}] Topic: '{topic}'")
        print(f"    Scenario: '{scenario}'")
        
        # Phase 1: Generate synthetic clinical presentation query
        try:
            simulated_query = generate_synthetic_query(topic, scenario)
            print(f"    Simulated Query: {repr(simulated_query)}")
        except Exception as e:
            print(f"    [ERR] Simulated Query Generation Failed: {e}")
            continue
            
        # Phase 2: Run through FHIR converter and extract scenario
        try:
            bundle = convert_text_to_fhir_bundle(simulated_query)
            bundle_dict = bundle.model_dump()
            extracted_scenario = extract_scenario_from_bundle(bundle_dict)
            print(f"    Extracted Scenario for RAG: {repr(extracted_scenario)}")
        except Exception as e:
            print(f"    [ERR] FHIR Conversion/Extraction Failed: {e}")
            continue
            
        # Phase 3: Query Vector DB and extract candidate scenarios
        query_emb = retriever.embeddings.embed_query(extracted_scenario)
        probe_tables = retriever.db.similarity_search_by_vector(
            query_emb, k=30, filter={"type": "variant_table"}
        )
        
        # Detect unique candidates
        unique_candidates = []
        for doc in probe_tables:
            sc = _extract_scenario(doc.page_content)
            tp = _extract_topic(doc.page_content)
            if sc and tp:
                pair = (tp, sc)
                if pair not in unique_candidates:
                    unique_candidates.append(pair)
                    
        # Check if expected topic is in top 3 unique candidates
        top_candidates = unique_candidates[:3]
        matched_rank = -1
        for rank, (cand_topic, cand_scenario) in enumerate(top_candidates):
            if cand_topic.lower() == topic.lower():
                matched_rank = rank + 1
                break
                
        is_passed = (matched_rank != -1)
        if is_passed:
            passed_count += 1
            status_str = f"PASS (Rank {matched_rank})"
        else:
            status_str = "FAIL"
            
        print(f"    Status: {status_str}")
        print("    Top Candidate Scenarios:")
        for r, (t, s) in enumerate(top_candidates):
            print(f"       [{r+1}] Topic='{t}', Scenario='{s}'")
            
        results.append({
            "topic": topic,
            "scenario": scenario,
            "simulated_query": simulated_query,
            "extracted_scenario": extracted_scenario,
            "status": status_str,
            "passed": is_passed,
            "matched_rank": matched_rank,
            "candidates": top_candidates
        })
        
    accuracy = (passed_count / len(test_cases)) * 100 if test_cases else 0
    print(f"\n{'=' * 70}")
    print(f"Validation Finished! accuracy: {accuracy:.2f}% ({passed_count}/{len(test_cases)} cases passed)")
    print(f"{'=' * 70}")
    
    # Write Markdown Report
    report_path = "evaluation_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# ACR-AC-RAG Batch Validation Report\n\n")
        f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"### Performance Summary\n")
        f.write(f"- **Total Cases Evaluated**: {len(test_cases)}\n")
        f.write(f"- **Passed Cases (Topic Matched in Top 3)**: {passed_count}\n")
        f.write(f"- **Failed Cases**: {len(test_cases) - passed_count}\n")
        f.write(f"- **Retrieval Accuracy**: **{accuracy:.2f}%**\n\n")
        
        f.write("## Detailed Test Cases\n\n")
        for idx, r in enumerate(results):
            status_color = "🟢" if r["passed"] else "🔴"
            f.write(f"### {idx+1}. {r['topic']} - {status_color} {r['status']}\n")
            f.write(f"- **Original ACR Scenario**: {r['scenario']}\n")
            f.write(f"- **Generated Simulated Query**: *\"{r['simulated_query']}\"*\n")
            f.write(f"- **FHIR Extracted Scenario**: `\"{r['extracted_scenario']}\"`\n")
            f.write("- **Retrieved Top 3 Candidates**:\n")
            for rank, (t, s) in enumerate(r["candidates"]):
                highlight = " **(MATCHED)**" if t.lower() == r["topic"].lower() else ""
                f.write(f"  {rank+1}. **Topic**: `{t}`, **Scenario**: `{s}`{highlight}\n")
            f.write("\n---\n\n")
            
    print(f"Evaluation report written to {report_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-size", type=int, default=15, help="Number of scenarios to test")
    args = parser.parse_args()
    run_validation(args.sample_size)
