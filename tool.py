# -*- coding: utf-8 -*-
"""版本与备份工具

用法：
    python tool.py save "改了啥"      保存一个版本（代码 + 数据库快照）
    python tool.py list               看历史版本
    python tool.py rollback v20261022-071900
                                      回退到某个版本（回退前会自动再存一份）
    python tool.py db                 看数据库备份
    python tool.py dbrestore db-v20261022-071900.db
                                      把数据库恢复到某个备份
"""
import glob
import os
import shutil
import sqlite3
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "canteen.db")
BK = os.path.join(BASE, "backups", "db")
KEEP = 40


def copy_db(src, dest):
    """用 SQLite 在线备份接口复制，WAL 模式下也不会漏数据"""
    s = sqlite3.connect(src, timeout=20)
    d = sqlite3.connect(dest)
    try:
        with d:
            s.backup(d)
    finally:
        d.close()
        s.close()


def git(args, check=False):
    p = subprocess.run(["git"] + args, cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        print((p.stdout or "") + (p.stderr or ""))
        sys.exit(1)
    return p


def ensure_repo():
    if os.path.isdir(os.path.join(BASE, ".git")):
        return
    git(["init"], check=True)
    git(["config", "user.name", "canteen"], check=True)
    git(["config", "user.email", "canteen@local"], check=True)
    print("已初始化版本库")


def save(note):
    ensure_repo()
    git(["add", "-A"])
    dirty = bool(git(["status", "--porcelain"]).stdout.strip())
    if dirty:
        git(["commit", "-m", note or "保存版本"], check=True)
        print("已提交改动：%s" % (note or "保存版本"))
    else:
        print("没有新的改动，只做数据库快照")
    tag = time.strftime("v%Y%m%d-%H%M%S")
    git(["tag", "-f", tag])
    os.makedirs(BK, exist_ok=True)
    if os.path.exists(DB):
        dest = os.path.join(BK, "db-%s.db" % tag)
        copy_db(DB, dest)
        print("数据库已备份：backups/db/db-%s.db" % tag)
    files = sorted(glob.glob(os.path.join(BK, "db-*.db")))
    for old in files[:-KEEP]:
        os.remove(old)
    print("版本号：%s" % tag)


def show_list():
    ensure_repo()
    log = git(["log", "--pretty=format:%h|%ad|%s", "--date=format:%m-%d %H:%M", "-30"]).stdout
    if not log.strip():
        print("还没有任何版本，先跑 python tool.py save \"初始版本\"")
        return
    print("最近版本（越靠上越新）：")
    for line in log.splitlines():
        h, when, msg = line.split("|", 2)
        tags = git(["tag", "--points-at", h]).stdout.split()
        tag = tags[0] if tags else ""
        print("  %-9s %s  %-18s %s" % (h, when, tag, msg))
    print("\n回退命令：python tool.py rollback <版本号或 v 开头的标签>")


def rollback(target):
    ensure_repo()
    print("回退前先自动备份当前状态…")
    save("回退前的自动备份")
    chk = git(["rev-parse", "--verify", target])
    if chk.returncode != 0:
        print("找不到这个版本：%s" % target)
        return
    git(["reset", "--hard", target], check=True)
    print("代码已回退到 %s" % target)
    print("如果线上也改了，需要重新发布一次才会生效。")


def db_list():
    files = sorted(glob.glob(os.path.join(BK, "db-*.db")))
    if not files:
        print("还没有数据库备份")
        return
    print("数据库备份（共 %d 份，保留最近 %d 份）：" % (len(files), KEEP))
    for f in reversed(files[-15:]):
        size = os.path.getsize(f) / 1024
        print("  %-32s %6.0f KB" % (os.path.basename(f), size))
    print("\n恢复命令：python tool.py dbrestore <文件名>")


def db_restore(name):
    path = name if os.path.isabs(name) else os.path.join(BK, os.path.basename(name))
    if not os.path.exists(path):
        print("找不到备份文件：%s" % name)
        return
    os.makedirs(BK, exist_ok=True)
    if os.path.exists(DB):
        copy_db(DB, os.path.join(BK, "db-before-restore-%s.db" % time.strftime("%Y%m%d-%H%M%S")))
    copy_db(path, DB)
    print("数据库已恢复自：%s" % os.path.basename(path))
    print("恢复前的数据库也另存了一份，在 backups/db/ 里。")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "save":
        save(sys.argv[2] if len(sys.argv) > 2 else "")
    elif cmd == "list":
        show_list()
    elif cmd == "rollback":
        if len(sys.argv) < 3:
            print("用法：python tool.py rollback <版本号>")
            return
        rollback(sys.argv[2])
    elif cmd == "db":
        db_list()
    elif cmd == "dbrestore":
        if len(sys.argv) < 3:
            print("用法：python tool.py dbrestore <文件名>")
            return
        db_restore(sys.argv[2])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
