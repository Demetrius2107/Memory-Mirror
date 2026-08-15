"""WS 冒烟测试：连接 /ws/progress，接收进度事件直到 done。"""

import asyncio
import json

import websockets

URL = "ws://127.0.0.1:8787/ws/progress"


async def main():
    async with websockets.connect(URL) as ws:
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            print(f"type={data.get('type'):<8} phase={data.get('phase','-'):<10} percent={data.get('percent','-')}")
            if data.get("type") == "done":
                break


if __name__ == "__main__":
    asyncio.run(main())
