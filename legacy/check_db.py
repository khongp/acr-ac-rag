import sqlite3

con = sqlite3.connect('chroma_db/chroma.sqlite3')
cur = con.cursor()
cur.execute("SELECT string_value FROM embedding_metadata WHERE key = 'scenario' LIMIT 5")
for row in cur.fetchall():
    print(row[0])
con.close()
