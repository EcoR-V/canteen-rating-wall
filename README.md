# 食堂评分墙 · Canteen Rating Wall

> 一个为大学食堂做的**匿名评分社区**：学生每天能匿名给窗口打分、发带图的美食帖，榜单把当月最受欢迎的窗口推上「C 位」，并自动生成可分享的月度海报。手机 / 电脑双端适配，开箱即用，零外部依赖。

---

## ✨ 功能特性

- **C 位榜单**：按当月平均分实时排名，第一名独占一张大卡片，其余分两列展示，一屏看完 30+ 个窗口。
- **月度海报区**：自动取出上月第一名，生成可直接转发到班级群 / 朋友圈的「月度最佳窗口」海报。
- **匿名打分**：学生免注册，系统自动分配一个随机昵称；每人每天最多打 3 次分，防刷分。
- **美食帖 + 配图**：可以给某个窗口发带照片的帖子（吐槽 / 安利都行），支持回复、点赞、举报。
- **商家后台**：每个窗口一个商家账号，能看自家评分、上传菜品图、回复学生帖子、重置登录口令。
- **管理员后台**：统一账号，可管理窗口、重置商家口令、处理举报、撤下不当帖子。
- **双端适配**：手机用底部导航栏，电脑端榜单自动分两列、信息密度更高。
- **零依赖运维**：Flask + SQLite，纯前端无构建步骤，一个 `python app.py` 就能跑。

---

## 🧱 技术栈

| 层 | 选型 |
|----|------|
| 后端 | Python 3 + Flask |
| 数据库 | SQLite（WAL 模式，自带并发保护） |
| 前端 | 原生 HTML / CSS / JavaScript（无框架、无打包） |
| 部署 | 任意支持 Python 的环境；也可一键发布到云端 |

---

## 🚀 快速开始

环境要求：Python 3.8 及以上。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动（首次启动会自动建库并写入演示数据）
python app.py

# 3. 浏览器打开
#    本机：  http://127.0.0.1:5001
#    局域网：把 app.py 底部的 HOST 改成 0.0.0.0 后重新运行
```

> 不想配 Python 环境？双击 `start.bat`（Windows）会自动建虚拟环境并安装依赖。

---

## 🔑 默认账号（演示用）

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | `admin` | `admin@2026` |
| 商家（示例） | `c1-1-01` | `shop123456` |

> ⚠️ **安全提醒**：以上均为演示默认口令，仓库里的演示数据也是虚构的。
> **公网部署前务必改掉**：
> - 管理员：设环境变量 `ADMIN_PASSWORD=你的强口令` 后删掉 `canteen.db` 重启，或在后台「修改密码」里改；
> - 商家口令：登录管理员后台，对每个窗口点「重置商家口令」即可生成新密码。

---

## 📁 项目结构

```
canteen/
├── app.py              # Flask 后端：接口、鉴权、页面路由
├── database.py         # 建库、种子数据、时间/时区工具
├── static/             # 前端（无构建）
│   ├── index.html
│   ├── app.js
│   └── style.css
├── uploads/            # 演示菜品图与帖子配图（已随仓库提供）
├── tool.py             # 版本管理：save / rollback / db 快照
├── requirements.txt
├── start.bat           # Windows 一键启动
└── README.md
```

`canteen.db`、`secret.key`、`backups/` 均**不入库**——数据库由 `database.py` 自动初始化，密钥每台机器自行生成。

---

## ⚙️ 配置项（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `5001` | 监听端口 |
| `HOST` | `0.0.0.0` | 监听地址（`127.0.0.1` 仅本机） |
| `ADMIN_PASSWORD` | `admin@2026` | 管理员口令；设置后下次建库生效 |

---

## 🛠 版本与数据管理

`tool.py` 提供本地版本快照（与 git 互补，git 管代码、它管数据库）：

```bash
python tool.py save "改了榜单样式"   # 提交代码 + 打标签 + 备份数据库
python tool.py list                  # 看历史版本
python tool.py rollback v20261022-xxx # 回退代码到某版本
python tool.py db                    # 列出数据库快照
python tool.py dbrestore db-xxx.db   # 恢复某份数据库
```

---

## 📡 接口概览（RESTful JSON）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/login` | 学生 / 商家 / 管理员登录 |
| GET  | `/api/leaderboard` | 当月榜单 + 上月第一名 |
| GET  | `/api/windows` | 窗口列表 |
| GET  | `/api/windows/<id>` | 窗口详情与菜品 |
| POST | `/api/rate` | 匿名打分（需学生身份，每日限 3 次） |
| POST | `/api/posts` | 发帖（可带图） |
| POST | `/api/posts/<id>/reply` | 回复 |
| POST | `/api/posts/<id>/like` | 点赞 |
| POST | `/api/posts/<id>/report` | 举报 |
| 管理员 / 商家 | `/api/admin/*`、`/api/merchant/*` | 后台管理 |

---

## 📦 部署提示

- 生产环境建议在反向代理（Nginx / Caddy）后运行，并关闭 `debug`。
- 数据库文件 `canteen.db` 请定期备份（`tool.py db` 已自动保留最近 40 份快照）。
- 上传目录 `uploads/` 需保证可写。

---

## 📄 开源协议

[MIT License](./LICENSE) —— 可自由用于学习、二次开发、校内推广，欢迎提 Issue 和 PR。

---

Made with ☕ for campus canteens everywhere.
