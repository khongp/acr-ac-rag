import urllib.request
from bs4 import BeautifulSoup
import json

html = urllib.request.urlopen('https://gravitas.acr.org/ACPortal/GetDataForOneTopic?topicId=396').read()
soup = BeautifulSoup(html, 'html.parser')

# Let's find tables that have a specific class or structure.
# Often, there are wrapping tables. Let's find the inner tables with class "table" or similar.
tables = soup.find_all('table')
print(f"Total tables: {len(tables)}")

output = []
# We just want a sample of the first 5 tables
for t in tables[:5]:
    rows = t.find_all('tr')
    table_data = []
    for r in rows:
        cols = r.find_all(['th', 'td'])
        # Extract text, stripping extra whitespace
        row_data = [c.get_text(" ", strip=True) for c in cols]
        if row_data:
            table_data.append(row_data)
    if table_data:
        output.append(table_data)

with open('topic_tables.json', 'w', encoding='utf-8') as f:
    json.dump(output, f, indent=2)
