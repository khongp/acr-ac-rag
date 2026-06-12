import sqlite3
con = sqlite3.connect('chroma_db/chroma.sqlite3')
cur = con.cursor()
# Count total variant_table metadata entries
cur.execute("SELECT count(*) FROM embedding_metadata WHERE key = 'type' AND string_value = 'variant_table'")
print("variant_table count:", cur.fetchone()[0])

# Check a few keys
cur.execute("SELECT DISTINCT key FROM embedding_metadata LIMIT 20")
print("keys:", cur.fetchall())

# Look for cauda equina in metadata or embeddings
cur.execute("SELECT id, string_value FROM embedding_metadata WHERE key = 'topic' AND string_value LIKE '%Low Back%' LIMIT 10")
print("Low Back topics:", cur.fetchall())

con.close()
