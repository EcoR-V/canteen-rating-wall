# 多人协作 · 在线编辑 · 版本回退

> 这个项目已经是一个 git 仓库了。下面所有命令都在 `canteen/` 目录里执行。

---

## 一、改完就存一个版本

```bash
python tool.py save "改了啥"
```

会做三件事：提交代码改动 → 打一个版本号（如 `v20261022-071957`）→ 把 `canteen.db` 复制一份到 `backups/db/`。

## 二、改崩了怎么救

**只撤一个文件的临时改动**
```bash
git checkout -- static/style.css
```

**整个项目退回某个版本**
```bash
python tool.py list                      # 看历史，找到版本号
python tool.py rollback v20261022-071957
```
回退前会自动再存一份当前状态，所以回退本身也能后悔。

**数据被改坏了**（比如管理员误删了窗口、评分被清空）
```bash
python tool.py db                        # 列出数据库快照
python tool.py dbrestore db-v20261022-071957.db
```
恢复前的数据库会另存一份，不怕覆盖错。

## 三、推到 GitHub（一次配置，之后随时同步）

1. 打开 github.com → New repository → 名字填 `canteen-rating-wall` → 选 **Public**（开源）→ 不要勾选任何初始化文件 → Create
2. 复制页面上的仓库地址，执行：
```bash
python github_push.py https://github.com/你的用户名/canteen-rating.git
```
第一次会弹 GitHub 登录窗口，授权一次。

之后每次改完：
```bash
python tool.py save "改了啥"
git push
```

## 四、在网上用 AI 编辑

**轻量版（改代码足够）**
打开仓库页面，按一下 `.` 键 → 浏览器里直接打开 VS Code。改完点 Source Control → Commit，一次提交就是一次备份。

**AI 助手**
在那个网页编辑器里装 GitHub Copilot 扩展，登录 GitHub 账号即可用中文下指令（学生认证免费，GitHub Student Developer Pack 里领）。比如选中一段 CSS 说"这个卡片在手机上太挤了，改成两列"。

**完整运行环境**
仓库页 → Code → Codespaces → Create。云端会给你一台完整机器，`pip install -r requirements.txt` 后 `python app.py` 就能跑，改完直接在里面预览。

## 五、几个人同时改同一件事

**异步协作（推荐，不会打架）**
```bash
git checkout -b 小李改榜单        # 每人开自己的分支
# 改完
python tool.py save "小李：榜单加了筛选"
git push -u origin 小李改榜单
```
然后在 GitHub 上开 Pull Request，另一个人看过再合并。谁改的、改了什么，全在记录里。

**实时协作（像 Google Docs 那样一起改）**
VS Code 装 **Live Share** 扩展 → 点 Share → 把链接发给对方。对方点开就能进到你的编辑器里，两边光标同时动。改完还是走上面的 commit 流程。

**真冲突了怎么办**
同一行被两个人改了，`git pull` 时 git 会在文件里标出 `<<<<<<<` `=======` `>>>>>>>`，保留你要的那段、删掉标记，再 `git add` + `commit` 即可。

## 六、几个约定

- `canteen.db` 不进 git —— 数据天天在变，入库会让仓库越来越臃肿。数据靠 `backups/db/` 快照 + `database.py` 自动建库。
- `secret.key` 不进 git —— 每台机器自己生成，别外传（它决定登录 cookie 的签名）。
- `backups/` 不进 git，只在本地。想异地备份，把整个 `backups/` 文件夹同步到网盘就行。
- 数据库快照保留最近 40 份，再多会自动删最旧的。

## 七、上线

改完本地验证没问题后，重新发布一次才会生效：
```bash
# 在 WorkBuddy 里让我发，或者
python tool.py save "发布前快照"
```
线上数据在沙箱里，跟本地 `canteen.db` 是两份，互不影响。
