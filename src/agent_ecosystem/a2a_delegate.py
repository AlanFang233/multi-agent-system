
import uuid

import httpx


async def send_a2a_text(
        url: str,
        prompt: str,
        *,
        metadata: dict | None = None,
        timeout: float = 120.0,
        return_status: bool = False,
    ) -> str:
    """通过 A2A JSON-RPC 1.0 发送任务，返回文本 Artifact。

    When ``return_status`` is True the return type is ``tuple[str, str]``
    (state, text) so callers can detect INPUT_REQUIRED without an exception.
    """

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": str(uuid.uuid4()),
                "role": "ROLE_USER",
                "parts": [{"text": prompt}],
                "metadata": metadata if metadata else {}
            },
            "configuration": {
                "blocking": True,
                "acceptedOutputModes": ["text/plain"],
            },
        },
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            json=payload,
            headers={"A2A-Version": "1.0"},
        )
        response.raise_for_status()
        data = response.json()

    if "error" in data:
        raise RuntimeError(f"A2A 调用失败: {data['error']}")

    result = data.get("result", {})
    task = result.get("task")

    if task:
        state = task.get("status", {}).get("state")
        print(
            f"[A2A] 下游 task_id={task.get('id')}, "
            f"state={state}",
            flush=True,
        )

        parts = [
            part["text"]
            for artifact in task.get("artifacts", [])
            for part in artifact.get("parts", [])
            if "text" in part
        ]

        if state not in ("TASK_STATE_COMPLETED", "completed"):
            if return_status:
                return state, "\n".join(parts)
            raise RuntimeError(
                f"下游任务尚未完成或执行失败: {state}"
            )
    else:
        state = "completed"
        message = result.get("message", result)
        parts = [
            part["text"]
            for part in message.get("parts", [])
            if "text" in part
        ]

    if not parts:
        if return_status:
            return state, ""
        raise RuntimeError("A2A 未返回文本结果")

    text = "\n".join(parts)
    if return_status:
        return state, text
    return text


async def delegate_to_coding(prompt: str) -> str:
    print("[A2A] Research -> Coding: 开始委托", flush=True)

    answer = await send_a2a_text(
        "http://127.0.0.1:8002",
        prompt,
    )

    print("[A2A] Research <- Coding: 收到结果", flush=True)
    return answer
