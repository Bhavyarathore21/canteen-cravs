import sys

from auth import hash_password
from database import get_connection

email = sys.argv[1].lower()
new_password = sys.argv[2]

conn = get_connection()
cur = conn.execute(
    "UPDATE users SET password_hash = ? WHERE email = ?",
    (hash_password(new_password), email),
)
conn.commit()
print("Password reset" if cur.rowcount else "No user with that email")
conn.close()