"""Smoke test for A2A agent at localhost:8001."""

import asyncio
import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest


async def main():
    async with httpx.AsyncClient() as http:
        card = await A2ACardResolver(
            httpx_client=http, base_url="http://127.0.0.1:8001"
        ).get_agent_card()
    client = await create_client(agent=card, client_config=ClientConfig(streaming=False))
    try:
        message = new_text_message("Summarize the purpose of A2A", role=Role.ROLE_USER)
        async for event in client.send_message(SendMessageRequest(message=message)):
            print(event)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
