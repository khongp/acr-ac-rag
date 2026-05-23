import sys, json
sys.stdout.reconfigure(encoding='utf-8')
from extract_acr_tables import extract_tables

extract_tables(output_file='data/test_141.json', start_topic_id=141, max_topic_id=141)

data = json.load(open('data/test_141.json','r',encoding='utf-8'))
print(f'\nTopics: {len(data)}')
for t in data:
    tid = t["topicId"]
    tname = t["topicName"]
    variants = t["variantData"]
    print(f'  {tid}: {tname} ({len(variants)} procs)')
    scenarios = set(v.get("Scenario", "?") for v in variants)
    print(f'  Unique scenarios: {len(scenarios)}')
    for s in scenarios:
        if 'cauda' in s.lower():
            print(f'    FOUND CAUDA EQUINA: {s}')
            for v in variants:
                if v.get("Scenario") == s:
                    cat = v.get("Appropriateness Category", "?")
                    proc = v.get("Procedure", "?")
                    print(f'      [{cat:25s}] {proc}')
