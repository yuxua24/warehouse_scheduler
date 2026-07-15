#!/usr/bin/env python3
"""微信扫码登录脚本 — 获取 iLink Bot API 凭证。

用法:
    python scripts/weixin_login.py [--output configs/weixin_account.json]
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from app.channels.weixin.auth import qr_login, save_account, QrLoginError


def print_status(status: str, data: dict) -> None:
    """状态回调：在终端打印进度。"""
    if status == "qrcode":
        url = data.get("qrcode_url", "")
        print("\n🔳 请用微信扫描以下二维码：")
        if url:
            print(f"   {url}")
        print()
        try:
            import qrcode
            qr = qrcode.QRCode(border=1)
            qr.add_data(url or data.get("qrcode_value", ""))
            qr.make(fit=True)
            qr.print_ascii(invert=True)
        except ImportError:
            print("   (安装 qrcode 可以显示终端二维码: pip install qrcode)")
        print("⏳ 等待扫码...", end="", flush=True)
        return

    if status == "wait":
        print(".", end="", flush=True)
    elif status == "scaned":
        print("\n📱 已扫码！请在微信中点击「确认登录」", end="", flush=True)
    elif status == "scaned_but_redirect":
        print("\n🔄 重定向...")
    elif status == "expired":
        print("\n⏰ 二维码过期，正在刷新...")
    elif status == "confirmed":
        print("\n🎉 确认成功！")
    elif status == "error":
        msg = data.get("message", "未知错误")
        print(f"\n⚠️ 临时错误: {msg}")
    else:
        print(f"\n状态: {status}")


async def main():
    parser = argparse.ArgumentParser(description="微信扫码登录 iLink Bot API")
    parser.add_argument(
        "--output", "-o",
        default=str(Path("configs") / "weixin_account.json"),
        help="保存账户凭证的 JSON 文件路径",
    )
    parser.add_argument(
        "--base-url",
        default="https://ilinkai.weixin.qq.com",
        help="iLink API 基础 URL",
    )
    args = parser.parse_args()

    print("=" * 50)
    print("  微信 iLink Bot 扫码登录")
    print("  仓储机器人调度系统")
    print("=" * 50)

    try:
        account = await qr_login(
            base_url=args.base_url,
            bot_type="3",
            timeout_seconds=480,
            on_status=print_status,
        )
    except QrLoginError as e:
        print(f"\n❌ 登录失败: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
        sys.exit(1)

    # 保存凭证
    output_path = Path(args.output)
    save_account(account, str(output_path))

    print()
    print("=" * 50)
    print(f"✅ 登录成功！凭证已保存到: {output_path}")
    print(f"   account_id: {account['account_id']}")
    print(f"   token:      {account['token'][:30]}...")
    print(f"   base_url:   {account['base_url']}")
    if account.get("user_id"):
        print(f"   user_id:    {account['user_id']}")
    print("=" * 50)

    config_path = output_path.parent / "weixin_config.json"
    print(f"\n下一步：编辑 {config_path}，设置:")
    print(f'  "enabled": true')
    print(f'  "account_id": "{account["account_id"]}"')
    print(f'  "token": "{account["token"]}"')
    print(f'  "allowed_users": ["<你的微信 user_id>"]')
    print()
    print("然后重启服务即可。")


if __name__ == "__main__":
    asyncio.run(main())
