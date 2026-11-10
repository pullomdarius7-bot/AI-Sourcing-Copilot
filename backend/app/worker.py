import asyncio

from app.jobs.worker import main

if __name__ == "__main__":
    asyncio.run(main())
