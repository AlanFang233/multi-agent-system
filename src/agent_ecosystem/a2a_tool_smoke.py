"""End-to-end A2A -> LLM -> MCP tool call smoke test for the Coding Agent."""
import argparse
import asyncio

import httpx
from a2a.client import A2ACardResolver, ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest



async def run(url: str, prompt: str) -> None:
    timeout = httpx.Timeout(
        connect=10.0,
        read=300.0,
        write=30.0,
        pool=10.0,
    )

    async with httpx.AsyncClient(timeout=timeout) as http:
        card = await A2ACardResolver(
            httpx_client=http,
            base_url=url,
        ).get_agent_card()

        client = await create_client(
            agent=card,
            client_config=ClientConfig(
                streaming=False,
                httpx_client=http,
            ),
        )

        try:
            message = new_text_message(
                prompt,
                role=Role.ROLE_USER,
            )

            async for event in client.send_message(
                SendMessageRequest(message=message)
            ):
                print(event)

        finally:
            await client.close()



def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--prompt", default="请检查当前项目是否有尚未提交的代码变更，并告诉我哪些文件发生了变化")
    args = parser.parse_args()
    asyncio.run(run(args.url, args.prompt))


if __name__ == "__main__":
    main()
