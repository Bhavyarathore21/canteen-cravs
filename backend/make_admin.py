import sys

from database import get_connection

email = sys.argv[1].lower()
conn = get_connection()
cur = conn.execute("UPDATE users SET role = 'admin' WHERE email = ?", (email,))
conn.commit()
print("Done, now an admin" if cur.rowcount else "No user with that email")
conn.close()