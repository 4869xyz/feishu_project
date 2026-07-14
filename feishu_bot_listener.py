import asyncio
import os
import sys

from dotenv import load_dotenv
from lark_channel import FeishuChannel


load_dotenv()

APP_ID = os.getenv("FEISHU_APP_ID")
APP_SECRET = os.getenv("FEISHU_APP_SECRET")

if not APP_ID or not APP_SECRET:
    print("错误：请先在 .env 中配置 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
    sys.exit(1)


channel = FeishuChannel(
    app_id=APP_ID,
    app_secret=APP_SECRET,
)


async def on_message(message) -> None:
    """
    收到机器人消息时执行。
    当前阶段只打印消息并回复，不进行文件下载。
    """
    print("=" * 60)
    print("收到飞书消息")
    print(message)
    print("=" * 60)

    try:
        await channel.send(
            message.chat_id,
            {
                "text": (
                    "机器人已收到你的消息。\n"
                    f"消息 ID：{message.message_id}"
                )
            },
            {
                "reply_to": message.message_id
            },
        )
    except Exception as exc:
        print(f"回复消息失败：{exc}")


async def on_error(error) -> None:
    print(f"飞书长连接发生异常：{error}")


async def main() -> None:
    channel.on("message", on_message)
    channel.on("error", on_error)

    print("正在连接飞书开放平台……")
    print("连接成功后，请保持本程序运行。")

    await channel.connect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序已停止。")