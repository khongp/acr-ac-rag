import sqlite3
import traceback
try:
    con = sqlite3.connect('chroma_db/chroma.sqlite3')
    cur = con.cursor()
    cur.execute("SELECT string_value FROM embedding_metadata WHERE key = 'type' AND string_value = 'variant_table'")
    rows = cur.fetchall()
    print(f"Total variant_table metadata entries: {len(rows)}")
    con.close()
except Exception as e:
    traceback.print_exc()
