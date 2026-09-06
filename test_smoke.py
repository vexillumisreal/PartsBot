import asyncio
import logging
from bot import bot, dp, on_startup, on_shutdown

logging.basicConfig(level=logging.INFO)

async def smoke_test():
    try:
        print("Starting smoke test...")
        await on_startup()
        print("Startup hooks executed successfully.")
        
        # Test bot token and get_me
        me = await bot.get_me()
        print(f"Bot authenticated as: @{me.username}")
        
    except Exception as e:
        print(f"Smoke test failed with error: {e}")
        raise e
    finally:
        await on_shutdown()
        print("Shutdown hooks executed successfully.")

if __name__ == "__main__":
    asyncio.run(smoke_test())
