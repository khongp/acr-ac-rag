"""
ACR Variant Table Scraper (v2) — Fixed
=======================================
Fixes:
  1. topicName extracted from Evidence Table URL params
  2. Correct column parsing — handles the 6-col/4-col/2-col row pattern
  3. Proper scenario carry-forward for continuation rows
"""
import os
import json
import time
import logging
import requests
from bs4 import BeautifulSoup
import re

def extract_tables(output_file="data/acr_variant_tables.json", start_topic_id=1, max_topic_id=2000):
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    
    all_data = []
    consecutive_errors = 0
    
    logging.info(f"Starting ACR table extraction v2 → {os.path.abspath(output_file)}")
    logging.info(f"Scanning topic IDs {start_topic_id} to {max_topic_id}")
    
    for topic_id in range(start_topic_id, max_topic_id + 1):
        if consecutive_errors > 500:
            logging.info("Stopping after 500 consecutive empty topics. Assuming end of records.")
            break
            
        url = f"https://gravitas.acr.org/ACPortal/GetDataForOneTopic?topicId={topic_id}"
        
        try:
            resp = session.get(url, timeout=15)
            
            if resp.status_code != 200:
                consecutive_errors += 1
                time.sleep(0.1)
                continue
                
            soup = BeautifulSoup(resp.text, 'html.parser')
            
            # ─── Extract Topic Name ───
            topic_name = None
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                if 'TopicName=' in href:
                    match = re.search(r'TopicName=([^&]+)', href)
                    if match:
                        topic_name = requests.utils.unquote(match.group(1)).strip()
                        break
            if not topic_name:
                topic_name = f"Topic {topic_id}"
            
            # ─── Extract Variant Scenario Links ───
            variant_scenarios = {}
            for a in soup.find_all('a', href=True):
                href = a.get('href', '')
                vm = re.search(r'#variant(\d+)', href)
                if vm:
                    vid = vm.group(1)
                    text = a.get_text(strip=True)
                    if text and vid not in variant_scenarios:
                        variant_scenarios[vid] = text
            
            if not variant_scenarios:
                consecutive_errors += 1
                time.sleep(0.1)
                continue
            
            # ─── Find all data tables ───
            # It's ALL tables with "Appropriateness Category" in headers
            data_tables = []
            for table in soup.find_all('table'):
                first_row = table.find('tr')
                if first_row:
                    header_text = first_row.get_text(" ", strip=True)
                    if "Appropriateness Category" in header_text:
                        data_tables.append(table)
            
            if not data_tables:
                consecutive_errors += 1
                time.sleep(0.1)
                continue
            
            # ─── Parse rows ───
            # Row patterns:
            #   6 cells: [Scenario, ScenarioID, Procedure, AdultRRL, PedsRRL, Category] — first proc of a variant
            #   4 cells: [Procedure, AdultRRL, PedsRRL, Category] — continuation proc for same variant
            #   2 cells: mobile responsive duplicate — SKIP
            
            topic_variants = []
            current_scenario = None
            current_scenario_id = None
            
            for main_table in data_tables:
                rows = main_table.find_all('tr')
                for row in rows[1:]:  # skip header row
                    cells = row.find_all('td', recursive=False)
                    
                    # Extract text from each cell
                    # The Procedure column has a nested responsive table containing the procedure name.
                    # We must NOT decompose it — instead, get text from direct children first,
                    # and fall back to the nested table's content.
                    cell_texts = []
                    for cell in cells:
                        nested_tables = cell.find_all('table')
                        if nested_tables:
                            # This cell has a responsive sub-table. 
                            # The procedure name is usually the first text in the sub-table.
                            # Get ALL text from the cell (including nested tables)
                            full_text = cell.get_text(" ", strip=True)
                            # The nested table duplicates the procedure name — just take the first occurrence
                            cell_texts.append(full_text.split("  ")[0].strip() if full_text else "")
                        else:
                            cell_texts.append(cell.get_text(" ", strip=True))
                    
                    n = len(cell_texts)
                    
                    if n == 6:
                        # Full row: Scenario, ScenarioID, Procedure, AdultRRL, PedsRRL, Category
                        current_scenario = cell_texts[0]
                        current_scenario_id = cell_texts[1]
                        
                        # Look up the full scenario description from variant links
                        if current_scenario_id and current_scenario_id in variant_scenarios:
                            current_scenario = variant_scenarios[current_scenario_id]
                        
                        procedure = cell_texts[2].strip()
                        category = cell_texts[5].strip()
                        
                        if procedure and category:
                            topic_variants.append({
                                "Scenario": current_scenario,
                                "Scenario ID": current_scenario_id,
                                "Procedure": procedure,
                                "Adult RRL": cell_texts[3].strip(),
                                "Peds RRL": cell_texts[4].strip(),
                                "Appropriateness Category": category,
                            })
                    
                    elif n == 4:
                        # Continuation row: Procedure, AdultRRL, PedsRRL, Category
                        procedure = cell_texts[0].strip()
                        category = cell_texts[3].strip()
                        
                        if procedure and category and current_scenario:
                            topic_variants.append({
                                "Scenario": current_scenario,
                                "Scenario ID": current_scenario_id or "",
                                "Procedure": procedure,
                                "Adult RRL": cell_texts[1].strip(),
                                "Peds RRL": cell_texts[2].strip(),
                                "Appropriateness Category": category,
                            })
                    
                    # Skip 2-cell rows (mobile responsive duplicates)
            
            if topic_variants:
                all_data.append({
                    "topicId": topic_id,
                    "topicName": topic_name,
                    "variantData": topic_variants
                })
                
                logging.info(f"Topic {topic_id}: '{topic_name}' — {len(topic_variants)} procedures across {len(variant_scenarios)} variants")
                consecutive_errors = 0
            else:
                consecutive_errors += 1
                
            time.sleep(0.15)
            
        except Exception as e:
            logging.error(f"Topic {topic_id}: Error - {str(e)}")
            consecutive_errors += 1
            time.sleep(0.5)
    
    # Save
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)
        
    total = sum(len(t.get("variantData", [])) for t in all_data)
    logging.info(f"Done. {len(all_data)} topics, {total} total procedure records → {output_file}")

if __name__ == "__main__":
    extract_tables()
