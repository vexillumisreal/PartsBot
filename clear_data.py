# import asyncio removed
import sqlite3
from config import ADMIN_IDS

DB_NAME = "spare_parts.db"


def clear_test_data():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    tables_to_clear = ["orders", "order_items", "stock_history", "wholesale_requests", "low_stock_alerts", "reports"]

    for table in tables_to_clear:
        try:
            cursor.execute(f"DELETE FROM {table}")
            print(f"Cleared table: {table}")
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                print(f"Skipping table {table} (does not exist)")
            else:
                raise

    # Reset all parts quantities to 0
    try:
        cursor.execute("UPDATE parts SET quantity = 0")
        print("Reset all parts quantities to 0.")
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            print("Skipping parts reset (does not exist)")
        else:
            raise

    # Delete users except admins
    if ADMIN_IDS:
        placeholders = ",".join("?" for _ in ADMIN_IDS)
        cursor.execute(f"DELETE FROM users WHERE user_id NOT IN ({placeholders})", ADMIN_IDS)
    else:
        cursor.execute("DELETE FROM users")

    print("Cleared users (retained admins).")

    conn.commit()
    conn.close()
    print("Database cleared successfully.")


if __name__ == "__main__":
    clear_test_data()
