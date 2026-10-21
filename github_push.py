# -*- coding: utf-8 -*-
"""把项目推到 GitHub（首次配置 + 后续同步）

用法：
    python github_push.py https://github.com/你的用户名/canteen-rating.git
"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))


def git(args, check=False):
    p = subprocess.run(["git"] + args, cwd=BASE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        print((p.stdout or "") + (p.stderr or ""))
    return p


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("仓库地址在 GitHub 建仓后页面上能复制，形如：")
        print("  https://github.com/你的用户名/canteen-rating.git")
        return

    url = sys.argv[1].strip()
    if not url.endswith(".git"):
        url += ".git"

    if not os.path.isdir(os.path.join(BASE, ".git")):
        git(["init"], check=True)
        git(["config", "user.name", "canteen"], check=True)
        git(["config", "user.email", "canteen@local"], check=True)

    # 统一用 main 分支
    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if branch and branch != "main":
        git(["branch", "-M", "main"], check=True)

    if not git(["remote"]).stdout.strip():
        git(["remote", "add", "origin", url], check=True)
        print("已关联远程仓库：%s" % url)
    else:
        git(["remote", "set-url", "origin", url], check=True)
        print("已更新远程地址：%s" % url)

    print("正在推送…（第一次会弹出 GitHub 登录窗口，授权一次就行）")
    p = git(["push", "-u", "origin", "main"])
    out = (p.stdout or "") + (p.stderr or "")
    print(out.strip())

    if p.returncode == 0:
        print("\n推送成功。以后改完代码，跑这两条就会同步：")
        print("  python tool.py save \"这次改了啥\"")
        print("  git push")
    else:
        low = out.lower()
        if "authentication" in low or "could not read" in low or "403" in low:
            print("\n推送失败：GitHub 认证没过。两种办法：")
            print("1) 让弹出的登录窗口走完（推荐）")
            print("2) 用令牌：GitHub → Settings → Developer settings → Personal access tokens")
            print("   生成一个有 repo 权限的 token，推送时用户名填 GitHub 用户名、密码填 token")
        elif "rejected" in low or "fetch first" in low:
            print("\n推送被拒：远程仓库已经有内容了。先执行：git pull --rebase origin main")
        else:
            print("\n推送失败，把上面这段报错发我。")


if __name__ == "__main__":
    main()
