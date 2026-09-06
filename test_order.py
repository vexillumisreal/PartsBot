import asyncio
import db

async def main():
    await db.init_db()
    await db.add_user(12345, 'test', 'Test User')
    print('User added')
    order_id = await db.create_order(12345, 'Test User', '123', 'pickup', 'addr', 'cash', 100)
    print(f'Order {order_id} created')
    
    # Clean up test user and order
    import sqlite3
    conn = sqlite3.connect(db.DB_NAME)
    conn.execute("DELETE FROM orders WHERE id=?", (order_id,))
    conn.execute("DELETE FROM users WHERE user_id=12345")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
