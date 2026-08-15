"""生成 WeChatMsg(留痕) 格式样例导入文件，用于验证导入通道。

输出: data/samples/wechatmsg_messages.csv + wechatmsg_contacts.csv + wechatmsg_members.csv
"""

import csv
from pathlib import Path

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"

# (createTime, msgType, strContent, from) —— WeChatMsg 常见导出列
MESSAGES = [
    ("2024-01-03 09:12:00", "1", "早上好，今天记得带伞", "wxid_demo_a"),
    ("2024-01-03 09:12:05", "1", "好的，我的手机 13812345678 记一下", "wxid_demo_a"),
    ("2024-01-05 22:30:00", "1", "晚安，明天见", "wxid_demo_a"),
    ("2024-01-03 09:12:00", "1", "早上好，今天记得带伞", "wxid_demo_a"),  # 内容重复 → 派生 id 相同 → 去重
    ("2024-02-14 18:00:00", "1", "情人节快乐！爱你", "wxid_demo_a"),
    ("2024-02-15 10:00:00", "3", "", "wxid_demo_a"),  # 图片消息（空文本允许）
    ("2024-03-01 08:00:00", "1", "身份证 11010119900307789X 已发送", "wxid_demo_b"),
    ("2024-03-02 20:15:00", "1", "方案发邮箱 test@example.com 了", "wxid_demo_b"),
    ("2025-06-01 12:00:00", "1", "好久不见，最近怎么样", "wxid_demo_b"),
    ("2024-04-01 10:00:00", "1", "大家周末有空吗", "chatroom_grp_demo"),
    ("2024-04-01 10:05:00", "1", "周末可以，去哪里", "chatroom_grp_demo"),
]

# (wxid, nickname, remark, type)
CONTACTS = [
    ("wxid_demo_a", "DemoA", "演示A", "friend"),
    ("wxid_demo_b", "DemoB", "演示B", "friend"),
    ("chatroom_grp_demo", "演示群", "演示群", "group"),
]

# (group_wxid, member_wxid, member_name)
MEMBERS = [
    ("chatroom_grp_demo", "wxid_demo_a", "演示A"),
    ("chatroom_grp_demo", "wxid_demo_b", "演示B"),
]


def main() -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    with open(SAMPLE_DIR / "wechatmsg_messages.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["createTime", "msgType", "strContent", "from"])
        w.writerows(MESSAGES)

    with open(SAMPLE_DIR / "wechatmsg_contacts.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["wxid", "nickname", "remark", "type"])
        w.writerows(CONTACTS)

    with open(SAMPLE_DIR / "wechatmsg_members.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group_wxid", "member_wxid", "member_name"])
        w.writerows(MEMBERS)

    print(f"样例已生成: {SAMPLE_DIR}")


if __name__ == "__main__":
    main()
