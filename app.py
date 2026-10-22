# -*- coding: utf-8 -*-
"""食堂匿名打分系统 —— Flask 后端

三身份：
  - 学生：无需注册，首次访问自动生成随机匿名代号，服务器不采集任何真实信息
  - 商家：管理员预建账号（c{食堂}-{楼层}-{窗口号}），管理本窗口菜品与照片
  - 管理员：窗口管理 / 商家账号重置 / 举报处理 / 删帖 / 看板
规则：
  - 1~5 星评分；同一窗口每天最多 1 次；每天最多给 3 个不同窗口打分
  - 月度榜单按自然月聚合，每月 1 日自动切换，历史月榜可查
"""
import os
import re
import secrets
import sqlite3
import string
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import wraps

CN_TZ = timezone(timedelta(hours=8))

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "canteen.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
SECRET_FILE = os.path.join(BASE_DIR, "secret.key")
STATIC_DIR = os.path.join(BASE_DIR, "static")

database.DB_PATH = DB_PATH

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="")


@app.after_request
def no_cache_static(resp):
    """前端静态资源禁用强缓存，保证发布新版后学生浏览器即时生效"""
    if request.path.endswith((".css", ".js")) or request.path == "/":
        resp.headers["Cache-Control"] = "no-cache"
    return resp
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024  # 照片上传上限 6MB（前端已压缩，双保险）
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.json.ensure_ascii = False


def _load_secret():
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_FILE, "w", encoding="utf-8") as f:
        f.write(key)
    return key


app.secret_key = _load_secret()

DAILY_WINDOW_QUOTA = 3  # 每天最多给几个窗口打分
ALLOWED_PHOTO_EXT = {"png", "jpg", "jpeg", "webp"}
REPORT_REASONS = ["垃圾广告", "人身攻击/引战", "不实信息", "色情低俗", "其他"]


# ---------------------------------------------------------------- 基础工具
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now_cn():
    """统一按东八区取时间：服务器在 UTC 时，日期边界不能跟着漂"""
    return datetime.now(CN_TZ)


def now():
    return _now_cn().strftime("%Y-%m-%d %H:%M:%S")


def today_str():
    return _now_cn().date().isoformat()


def this_month():
    return _now_cn().strftime("%Y-%m")


def ok(data=None):
    return jsonify({"ok": True, "data": data})


def err(msg, status=400):
    return jsonify({"ok": False, "msg": msg}), status


def current_anon_id():
    """确保当前浏览器存在匿名身份（不存在则自动创建）。仅返回数据库自增 id，与真实身份无任何关联。"""
    aid = session.get("anon_id")
    if aid:
        with get_db() as c:
            row = c.execute("SELECT id FROM anons WHERE id=?", (aid,)).fetchone()
            if row:
                return aid
    token = secrets.token_hex(16)
    nickname = database.gen_nickname()
    with get_db() as c:
        cur = c.execute("INSERT INTO anons(token, nickname, created_at) VALUES (?,?,?)",
                        (token, nickname, now()))
        aid = cur.lastrowid
    session["anon_id"] = aid
    return aid


def get_nickname(conn, anon_id):
    row = conn.execute("SELECT nickname FROM anons WHERE id=?", (anon_id,)).fetchone()
    return row["nickname"] if row else "匿名同学"


def quota_state(conn, anon_id):
    """返回 (今日已打分窗口 id 列表, 今日剩余次数)"""
    rated = [r["window_id"] for r in conn.execute(
        "SELECT DISTINCT window_id FROM ratings WHERE anon_id=? AND date=?",
        (anon_id, today_str()))]
    return rated, DAILY_WINDOW_QUOTA - len(rated)


def require_admin(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            return err("需要管理员身份登录", 403)
        return fn(*args, **kwargs)
    return wrapper


def require_merchant(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("role") != "merchant":
            return err("需要商家身份登录", 403)
        return fn(*args, **kwargs)
    return wrapper


def require_anon(fn):
    """学生侧写操作：商家/管理员账号不能参与打分、发帖、点赞、举报"""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        role = session.get("role")
        if role == "merchant":
            return err("当前是商家账号，不能以学生身份评分或发言", 403)
        if role == "admin":
            return err("当前是管理员账号，不能以学生身份评分或发言", 403)
        return fn(*args, **kwargs)
    return wrapper


def admin_log(conn, action, detail):
    conn.execute("INSERT INTO admin_logs(admin_user, action, detail, created_at) VALUES (?,?,?,?)",
                 (session.get("uid") and _admin_name(conn) or "admin", action, detail, now()))


def _admin_name(conn):
    row = conn.execute("SELECT username FROM admins WHERE id=?", (session.get("uid"),)).fetchone()
    return row["username"] if row else "admin"


def month_scores(conn, month, window_id=None):
    sql = ("SELECT r.window_id, ROUND(AVG(r.score),2) AS avg_score, COUNT(*) AS cnt "
           "FROM ratings r WHERE strftime('%Y-%m', r.date)=? ")
    args = [month]
    if window_id:
        sql += " AND r.window_id=? "
        args.append(window_id)
    sql += " GROUP BY r.window_id ORDER BY avg_score DESC, cnt DESC"
    return conn.execute(sql, args).fetchall()


def window_rank_map(conn, month):
    return {row["window_id"]: i + 1 for i, row in enumerate(month_scores(conn, month))}


# ---------------------------------------------------------------- 页面
@app.route("/")
def index():
    database.init_db()
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    # 以静态资源 mtime 作为版本号，发布新版后所有学生浏览器自动拉取最新前端
    v = max(int(os.path.getmtime(os.path.join(STATIC_DIR, f)))
            for f in ("app.js", "style.css"))
    html = html.replace("style.css", "style.css?v=%d" % v).replace("app.js", "app.js?v=%d" % v)
    return html


@app.route("/uploads/<path:filename>")
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/api/health")
def api_health():
    with get_db() as c:
        n = c.execute("SELECT COUNT(*) cnt FROM windows").fetchone()["cnt"]
    return ok({"time": now(), "windows": n})


# ---------------------------------------------------------------- 学生：身份与登录
@app.route("/api/bootstrap")
def api_bootstrap():
    aid = current_anon_id()
    with get_db() as c:
        nickname = get_nickname(c, aid)
        rated, left = quota_state(c, aid)
        months = [r["m"] for r in c.execute(
            "SELECT DISTINCT strftime('%Y-%m', date) AS m FROM ratings ORDER BY m DESC")]
    if this_month() not in months:
        months.insert(0, this_month())
    resp = {
        "nickname": nickname,
        "role": session.get("role", "anon"),
        "today_rated": rated,
        "quota_left": left,
        "quota_total": DAILY_WINDOW_QUOTA,
        "today": today_str(),
        "this_month": this_month(),
        "months": months,
    }
    if session.get("role") == "admin":
        row = None
        with get_db() as c:
            row = c.execute("SELECT username FROM admins WHERE id=?", (session.get("uid"),)).fetchone()
        resp["admin_name"] = row["username"] if row else "admin"
    elif session.get("role") == "merchant":
        row = None
        with get_db() as c:
            row = c.execute("SELECT name FROM windows WHERE id=?", (session.get("uid"),)).fetchone()
        resp["merchant_window"] = row["name"] if row else ""
    return ok(resp)


@app.route("/api/anon/reset", methods=["POST"])
def api_anon_reset():
    """换一个全新匿名身份（旧身份数据仍留存但不再关联）"""
    old = session.get("anon_id")
    token = secrets.token_hex(16)
    nickname = database.gen_nickname()
    with get_db() as c:
        cur = c.execute("INSERT INTO anons(token, nickname, created_at) VALUES (?,?,?)",
                        (token, nickname, now()))
        session["anon_id"] = cur.lastrowid
    return ok({"nickname": nickname, "old_anon": old})


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(force=True, silent=True) or {}
    role = data.get("role")
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if role not in ("admin", "merchant"):
        return err("身份类型错误")
    if not username or not password:
        return err("请输入账号和密码")
    with get_db() as c:
        if role == "admin":
            row = c.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
            if not row or not check_password_hash(row["password_hash"], password):
                return err("账号或密码错误", 401)
            session["role"] = "admin"
            session["uid"] = row["id"]
            return ok({"role": "admin", "username": row["username"]})
        row = c.execute("SELECT * FROM windows WHERE merchant_user=?", (username,)).fetchone()
        if not row or not check_password_hash(row["merchant_pass_hash"], password):
            return err("账号或密码错误", 401)
        if row["status"] != 1:
            return err("该窗口已被停用，请联系管理员", 403)
        session["role"] = "merchant"
        session["uid"] = row["id"]  # uid = 窗口 id
        return ok({"role": "merchant", "username": row["merchant_user"],
                   "window": _window_brief(row)})


def _window_brief(row):
    return {"id": row["id"], "canteen": row["canteen"], "floor": row["floor"],
            "win_no": row["win_no"], "name": row["name"], "merchant_name": row["merchant_name"],
            "merchant_user": row["merchant_user"], "status": row["status"]}


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session["role"] = "anon"
    session.pop("uid", None)
    return ok({"role": "anon"})


# ---------------------------------------------------------------- 学生：榜单与浏览
@app.route("/api/leaderboard")
def api_leaderboard():
    month = request.args.get("month") or this_month()
    if not re.match(r"^\d{4}-\d{2}$", month):
        return err("月份格式错误")
    aid = current_anon_id()
    with get_db() as c:
        rows = c.execute(
            "SELECT w.*, ROUND(AVG(r.score),2) AS avg_score, COUNT(*) AS cnt "
            "FROM ratings r JOIN windows w ON w.id=r.window_id "
            "WHERE strftime('%Y-%m', r.date)=? GROUP BY w.id ORDER BY avg_score DESC, cnt DESC",
            (month,)).fetchall()
        rated, left = quota_state(c, aid)
        months = [r["m"] for r in c.execute(
            "SELECT DISTINCT strftime('%Y-%m', date) AS m FROM ratings ORDER BY m DESC")]
        rank_map = window_rank_map(c, month)

        # 每个窗口的封面图：只用真实照片，自动生成的菜品卡不当作封面
        cover = {}
        for row in c.execute("SELECT window_id, photo FROM dishes "
                             "WHERE status=1 AND photo<>'' AND photo NOT LIKE 'auto-%' "
                             "ORDER BY window_id, id"):
            cover.setdefault(row["window_id"], row["photo"])

        # 上一期榜单（早于当前查询月里最近的一个月），用于「往期冠军海报」
        prev_months = [m for m in months if m < month]
        last_month = prev_months[0] if prev_months else None
        last_rows = []
        if last_month:
            last_rank = window_rank_map(c, last_month)
            for r in c.execute(
                    "SELECT w.*, ROUND(AVG(r.score),2) AS avg_score, COUNT(*) AS cnt "
                    "FROM ratings r JOIN windows w ON w.id=r.window_id "
                    "WHERE strftime('%Y-%m', r.date)=? GROUP BY w.id "
                    "ORDER BY avg_score DESC, cnt DESC LIMIT 6", (last_month,)).fetchall():
                last_rows.append({
                    "rank": last_rank[r["id"]], "window_id": r["id"], "name": r["name"],
                    "merchant_name": r["merchant_name"], "canteen": r["canteen"],
                    "floor": r["floor"], "win_no": r["win_no"], "avg_score": r["avg_score"],
                    "cnt": r["cnt"], "photo": cover.get(r["id"], "")})
    result = []
    for r in rows:
        result.append({"rank": rank_map[r["id"]], "window_id": r["id"], "name": r["name"],
                       "merchant_name": r["merchant_name"], "canteen": r["canteen"],
                       "floor": r["floor"], "win_no": r["win_no"],
                       "avg_score": r["avg_score"], "cnt": r["cnt"], "status": r["status"],
                       "photo": cover.get(r["id"], "")})
    if this_month() not in months:
        months.insert(0, this_month())
    nxt = datetime.strptime(month + "-01", "%Y-%m-%d")
    if nxt.month == 12:
        nxt = nxt.replace(year=nxt.year + 1, month=1)
    else:
        nxt = nxt.replace(month=nxt.month + 1)
    return ok({"month": month, "rows": result, "months": sorted(set(months), reverse=True),
               "next_update": nxt.strftime("%Y-%m-%d %H:%M:%S"),
               "today_rated": rated, "quota_left": left,
               "last_month": last_month, "last_rows": last_rows})


@app.route("/api/canteens")
def api_canteens():
    aid = current_anon_id()
    month = this_month()
    with get_db() as c:
        wins = c.execute("SELECT * FROM windows ORDER BY canteen, floor, win_no").fetchall()
        rank_map = window_rank_map(c, month)
        avg_map = {r["window_id"]: r for r in month_scores(c, month)}
        dish_cnt = {r["window_id"]: r["cnt"] for r in c.execute(
            "SELECT window_id, COUNT(*) cnt FROM dishes WHERE status=1 GROUP BY window_id")}
        rated, left = quota_state(c, aid)
    result = []
    for w in wins:
        avg = avg_map.get(w["id"])
        result.append({**_window_brief(w), "avg_score": avg["avg_score"] if avg else None,
                       "month_cnt": avg["cnt"] if avg else 0, "rank": rank_map.get(w["id"]),
                       "dish_cnt": dish_cnt.get(w["id"], 0)})
    return ok({"windows": result, "today_rated": rated, "quota_left": left,
               "this_month": month, "today": today_str()})


@app.route("/api/search")
def api_search():
    """关键词搜索窗口：匹配窗口名 / 商家名 / 菜品名 / 菜品简介，纯数字时再按窗口编号匹配"""
    q = (request.args.get("q") or "").strip()[:40]
    if not q:
        return ok({"q": "", "windows": [], "total": 0})
    # 转义 LIKE 通配符，避免用户输入的 % _ 被当成模式
    safe = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = "%" + safe + "%"
    month = this_month()

    with get_db() as c:
        hit_ids = [r["id"] for r in c.execute(
            "SELECT DISTINCT w.id FROM windows w "
            "LEFT JOIN dishes d ON d.window_id = w.id AND d.status = 1 "
            "WHERE w.name LIKE ? ESCAPE '\\' "
            "   OR w.merchant_name LIKE ? ESCAPE '\\' "
            "   OR d.name LIKE ? ESCAPE '\\' "
            "   OR d.intro LIKE ? ESCAPE '\\' "
            "ORDER BY w.id",
            (like, like, like, like))]
        # 纯数字：额外按窗口编号匹配（比如直接搜「12」）
        if q.isdigit():
            for r in c.execute("SELECT id FROM windows WHERE win_no = ?", (int(q),)):
                if r["id"] not in hit_ids:
                    hit_ids.append(r["id"])
        if not hit_ids:
            return ok({"q": q, "windows": [], "total": 0})

        ph = ",".join("?" * len(hit_ids))
        wins = c.execute("SELECT * FROM windows WHERE id IN (%s)" % ph, hit_ids).fetchall()
        avg_map = {r["window_id"]: r for r in month_scores(c, month)}
        rank_map = window_rank_map(c, month)
        dish_cnt = {r["window_id"]: r["cnt"] for r in c.execute(
            "SELECT window_id, COUNT(*) AS cnt FROM dishes WHERE status = 1 "
            "AND window_id IN (%s) GROUP BY window_id" % ph, hit_ids)}
        matched = {}
        for r in c.execute(
                "SELECT window_id, name, price FROM dishes "
                "WHERE status = 1 AND window_id IN (%s) "
                "  AND (name LIKE ? ESCAPE '\\' OR intro LIKE ? ESCAPE '\\') "
                "ORDER BY window_id, id" % ph, hit_ids + [like, like]):
            matched.setdefault(r["window_id"], []).append(
                {"name": r["name"], "price": r["price"]})

    ql = q.lower()
    result = []
    for w in wins:
        avg = avg_map.get(w["id"])
        result.append({
            **_window_brief(w),
            "avg_score": avg["avg_score"] if avg else None,
            "month_cnt": avg["cnt"] if avg else 0,
            "rank": rank_map.get(w["id"]),
            "dish_cnt": dish_cnt.get(w["id"], 0),
            "matched_dishes": matched.get(w["id"], [])[:4],
            "name_hit": ql in (w["name"] or "").lower(),
        })
    # 窗口名命中的排前面，其次按本月均分（无评分排后），最后按窗口号
    result.sort(key=lambda x: (0 if x["name_hit"] else 1,
                               -(x["avg_score"] if x["avg_score"] is not None else -1),
                               x["id"]))
    return ok({"q": q, "windows": result[:60], "total": len(result)})


@app.route("/api/windows/<int:wid>")
def api_window(wid):
    aid = current_anon_id()
    month = this_month()
    with get_db() as c:
        w = c.execute("SELECT * FROM windows WHERE id=?", (wid,)).fetchone()
        if not w:
            return err("窗口不存在", 404)
        dishes = c.execute(
            "SELECT * FROM dishes WHERE window_id=? AND status=1 ORDER BY id", (wid,)).fetchall()
        avg_row = c.execute(
            "SELECT ROUND(AVG(score),2) avg_score, COUNT(*) cnt FROM ratings "
            "WHERE window_id=? AND strftime('%Y-%m', date)=?", (wid, month)).fetchone()
        total_row = c.execute(
            "SELECT COUNT(*) cnt, ROUND(AVG(score),2) avg_score FROM ratings WHERE window_id=?",
            (wid,)).fetchone()
        rank_map = window_rank_map(c, month)
        my_today = c.execute(
            "SELECT score FROM ratings WHERE anon_id=? AND window_id=? AND date=?",
            (aid, wid, today_str())).fetchone()
        rated, left = quota_state(c, aid)
        post_cnt = c.execute("SELECT COUNT(*) cnt FROM posts WHERE window_id=? AND status=1 AND parent_id IS NULL",
                             (wid,)).fetchone()["cnt"]
    return ok({"window": {**_window_brief(w), "avg_score": avg_row["avg_score"],
                          "month_cnt": avg_row["cnt"], "total_cnt": total_row["cnt"],
                          "total_avg": total_row["avg_score"], "rank": rank_map.get(wid),
                          "post_cnt": post_cnt},
               "dishes": [dict(d) for d in dishes],
               "my_today_score": my_today["score"] if my_today else None,
               "today_rated": rated, "quota_left": left, "quota_total": DAILY_WINDOW_QUOTA})


@app.route("/api/windows/<int:wid>/posts")
def api_window_posts(wid):
    aid = current_anon_id()
    with get_db() as c:
        w = c.execute("SELECT id FROM windows WHERE id=?", (wid,)).fetchone()
        if not w:
            return err("窗口不存在", 404)
        mains = c.execute(
            "SELECT p.*, a.nickname FROM posts p JOIN anons a ON a.id=p.anon_id "
            "WHERE p.window_id=? AND p.parent_id IS NULL AND p.status=1 "
            "ORDER BY p.created_at DESC LIMIT 100", (wid,)).fetchall()
        main_ids = [p["id"] for p in mains]
        replies_map = {}
        likes_map = {}
        my_likes = set()
        if main_ids:
            qmarks = ",".join("?" * len(main_ids))
            replies = c.execute(
                "SELECT p.*, a.nickname FROM posts p JOIN anons a ON a.id=p.anon_id "
                "WHERE p.parent_id IN (%s) AND p.status=1 ORDER BY p.created_at" % qmarks,
                main_ids).fetchall()
            for r in replies:
                replies_map.setdefault(r["parent_id"], []).append(r)
            for row in c.execute(
                    "SELECT post_id, COUNT(*) cnt FROM post_likes WHERE post_id IN (%s) "
                    "GROUP BY post_id" % qmarks, main_ids):
                likes_map[row["post_id"]] = row["cnt"]
            for row in c.execute(
                    "SELECT post_id FROM post_likes WHERE anon_id=? AND post_id IN (%s)" % qmarks,
                    [aid] + main_ids):
                my_likes.add(row["post_id"])
        dishes = {d["id"]: d for d in c.execute(
            "SELECT id, name, price FROM dishes WHERE window_id=?", (wid,)).fetchall()}

        def dish_tag(dish_id):
            d = dishes.get(dish_id)
            return {"id": d["id"], "name": d["name"], "price": d["price"]} if d else None

        def to_post(p, is_reply=False):
            item = {"id": p["id"], "nickname": p["nickname"], "content": p["content"],
                    "photo": (p["photo"] or ""), "created_at": p["created_at"],
                    "dish": dish_tag(p["dish_id"]),
                    "likes": likes_map.get(p["id"], 0), "liked": p["id"] in my_likes,
                    "reply_count": len(replies_map.get(p["id"], []))}
            if not is_reply:
                item["replies"] = [to_post(r, True) for r in replies_map.get(p["id"], [])]
            return item

        return ok({"posts": [to_post(p) for p in mains]})


# ---------------------------------------------------------------- 学生：打分 / 发帖 / 点赞 / 举报
@app.route("/api/rate", methods=["POST"])
@require_anon
def api_rate():
    data = request.get_json(force=True, silent=True) or {}
    aid = current_anon_id()
    try:
        wid = int(data.get("window_id"))
        score = int(data.get("score"))
    except (TypeError, ValueError):
        return err("参数错误")
    if not 1 <= score <= 5:
        return err("评分需在 1~5 星之间")
    with get_db() as c:
        w = c.execute("SELECT * FROM windows WHERE id=?", (wid,)).fetchone()
        if not w:
            return err("窗口不存在", 404)
        if w["status"] != 1:
            return err("该窗口已停用")
        already = c.execute("SELECT score FROM ratings WHERE anon_id=? AND window_id=? AND date=?",
                            (aid, wid, today_str())).fetchone()
        if already:
            return err("今天已经给这个窗口打过 %d 星啦，明天再来" % already["score"])
        rated, left = quota_state(c, aid)
        if left <= 0:
            return err("今天 3 次打分机会已用完，明天再来吧")
        c.execute("INSERT INTO ratings(anon_id, window_id, score, date, created_at) VALUES (?,?,?,?,?)",
                  (aid, wid, score, today_str(), now()))
        avg_row = c.execute(
            "SELECT ROUND(AVG(score),2) avg_score, COUNT(*) cnt FROM ratings "
            "WHERE window_id=? AND strftime('%Y-%m', date)=?", (wid, this_month())).fetchone()
        rated.append(wid)
        return ok({"avg_score": avg_row["avg_score"], "month_cnt": avg_row["cnt"],
                   "today_rated": rated, "quota_left": DAILY_WINDOW_QUOTA - len(rated),
                   "my_today_score": score})


@app.route("/api/posts", methods=["POST"])
@require_anon
def api_create_post():
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get("content") or "").strip()
    if not 1 <= len(content) <= 500:
        return err("内容长度需在 1~500 字之间")
    try:
        wid = int(data.get("window_id"))
    except (TypeError, ValueError):
        return err("参数错误")
    dish_id = data.get("dish_id")
    aid = current_anon_id()
    with get_db() as c:
        w = c.execute("SELECT * FROM windows WHERE id=?", (wid,)).fetchone()
        if not w:
            return err("窗口不存在", 404)
        if w["status"] != 1:
            return err("该窗口已停用")
        if dish_id is not None:
            d = c.execute("SELECT id FROM dishes WHERE id=? AND window_id=? AND status=1",
                          (dish_id, wid)).fetchone()
            if not d:
                return err("关联的菜品不存在")
        c.execute("INSERT INTO posts(window_id, parent_id, dish_id, anon_id, content, created_at, status)"
                  " VALUES (?,?,?,?,?,?,1)", (wid, None, dish_id, aid, content, now()))
        pid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        nickname = get_nickname(c, aid)
        dish_row = None
        if dish_id is not None:
            dish_row = c.execute("SELECT id, name, price FROM dishes WHERE id=?", (dish_id,)).fetchone()
        return ok({"post": {"id": pid, "nickname": nickname, "content": content, "created_at": now(),
                           "dish": {"id": dish_row["id"], "name": dish_row["name"],
                                    "price": dish_row["price"]} if dish_row else None,
                           "likes": 0, "liked": False, "reply_count": 0, "replies": []}})


@app.route("/api/posts/<int:pid>/photo", methods=["POST"])
@require_anon
def api_post_upload_photo(pid):
    """学生给自己刚发的留言补一张照片（匿名身份校验，不能传别人的帖）"""
    aid = current_anon_id()
    f = request.files.get("photo")
    if not f or not f.filename:
        return err("请选择图片文件")
    ext = secure_filename(f.filename).rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_PHOTO_EXT:
        return err("仅支持 png / jpg / jpeg / webp 图片")
    with get_db() as c:
        p = c.execute("SELECT id FROM posts WHERE id=? AND anon_id=? AND status=1",
                      (pid, aid)).fetchone()
        if not p:
            return err("帖子不存在或不属于你", 404)
        fname = "post-%d-%s.%s" % (pid, secrets.token_hex(4), ext)
        f.save(os.path.join(UPLOAD_DIR, fname))
        c.execute("UPDATE posts SET photo=? WHERE id=?", (fname, pid))
        return ok({"photo": fname, "url": "/uploads/" + fname})


@app.route("/api/posts/<int:pid>/reply", methods=["POST"])
@require_anon
def api_reply(pid):
    data = request.get_json(force=True, silent=True) or {}
    content = (data.get("content") or "").strip()
    if not 1 <= len(content) <= 500:
        return err("内容长度需在 1~500 字之间")
    aid = current_anon_id()
    with get_db() as c:
        p = c.execute("SELECT * FROM posts WHERE id=? AND status=1 AND parent_id IS NULL", (pid,)).fetchone()
        if not p:
            return err("帖子不存在或已被删除", 404)
        w = c.execute("SELECT status FROM windows WHERE id=?", (p["window_id"],)).fetchone()
        if not w or w["status"] != 1:
            return err("该窗口已停用")
        c.execute("INSERT INTO posts(window_id, parent_id, dish_id, anon_id, content, created_at, status)"
                  " VALUES (?,?,?,?,?,?,1)", (p["window_id"], pid, None, aid, content, now()))
        rid = c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
        return ok({"reply": {"id": rid, "nickname": get_nickname(c, aid), "content": content,
                            "created_at": now(), "likes": 0, "liked": False}})


@app.route("/api/posts/<int:pid>/like", methods=["POST"])
@require_anon
def api_like(pid):
    aid = current_anon_id()
    with get_db() as c:
        p = c.execute("SELECT id FROM posts WHERE id=? AND status=1", (pid,)).fetchone()
        if not p:
            return err("帖子不存在或已被删除", 404)
        exist = c.execute("SELECT 1 FROM post_likes WHERE post_id=? AND anon_id=?", (pid, aid)).fetchone()
        if exist:
            c.execute("DELETE FROM post_likes WHERE post_id=? AND anon_id=?", (pid, aid))
            liked = False
        else:
            c.execute("INSERT INTO post_likes(post_id, anon_id, created_at) VALUES (?,?,?)",
                      (pid, aid, now()))
            liked = True
        cnt = c.execute("SELECT COUNT(*) cnt FROM post_likes WHERE post_id=?", (pid,)).fetchone()["cnt"]
        return ok({"liked": liked, "likes": cnt})


@app.route("/api/posts/<int:pid>/report", methods=["POST"])
@require_anon
def api_report(pid):
    data = request.get_json(force=True, silent=True) or {}
    reason = (data.get("reason") or "").strip()
    if not reason or len(reason) > 100:
        return err("请选择或填写举报理由（100 字内）")
    aid = current_anon_id()
    with get_db() as c:
        p = c.execute("SELECT id FROM posts WHERE id=? AND status=1", (pid,)).fetchone()
        if not p:
            return err("帖子不存在或已被删除", 404)
        dup = c.execute("SELECT 1 FROM reports WHERE post_id=? AND anon_id=? AND status=0",
                        (pid, aid)).fetchone()
        if dup:
            return err("你已经举报过这条帖子，管理员会尽快处理")
        c.execute("INSERT INTO reports(post_id, anon_id, reason, created_at, status) VALUES (?,?,?,?,0)",
                  (pid, aid, reason, now()))
        return ok({"message": "举报已提交，管理员将尽快处理"})


@app.route("/api/my")
def api_my():
    aid = current_anon_id()
    with get_db() as c:
        nickname = get_nickname(c, aid)
        rated, left = quota_state(c, aid)
        my_ratings = c.execute(
            "SELECT r.score, r.date, r.created_at, w.name AS window_name, w.id AS window_id, "
            "w.canteen, w.floor, w.win_no FROM ratings r JOIN windows w ON w.id=r.window_id "
            "WHERE r.anon_id=? ORDER BY r.created_at DESC LIMIT 100", (aid,)).fetchall()
        my_posts = c.execute(
            "SELECT p.id, p.content, p.created_at, p.parent_id, w.name AS window_name, w.id AS window_id "
            "FROM posts p JOIN windows w ON w.id=p.window_id "
            "WHERE p.anon_id=? AND p.status=1 ORDER BY p.created_at DESC LIMIT 100", (aid,)).fetchall()
        my_likes = c.execute("SELECT COUNT(*) cnt FROM post_likes WHERE anon_id=?", (aid,)).fetchone()["cnt"]
        created = c.execute("SELECT created_at FROM anons WHERE id=?", (aid,)).fetchone()["created_at"]
    return ok({"nickname": nickname, "created_at": created, "today_rated": rated,
               "quota_left": left, "quota_total": DAILY_WINDOW_QUOTA,
               "ratings": [dict(r) for r in my_ratings], "posts": [dict(p) for p in my_posts],
               "likes_given": my_likes})


# ---------------------------------------------------------------- 商家端
@app.route("/api/merchant/overview")
@require_merchant
def api_merchant_overview():
    wid = session.get("uid")
    with get_db() as c:
        w = c.execute("SELECT * FROM windows WHERE id=?", (wid,)).fetchone()
        if not w:
            return err("窗口不存在", 404)
        dishes = c.execute("SELECT * FROM dishes WHERE window_id=? ORDER BY status DESC, id", (wid,)).fetchall()
        avg = c.execute(
            "SELECT ROUND(AVG(score),2) avg_score, COUNT(*) cnt FROM ratings "
            "WHERE window_id=? AND strftime('%Y-%m', date)=?", (wid, this_month())).fetchone()
        total = c.execute("SELECT COUNT(*) cnt FROM ratings WHERE window_id=?", (wid,)).fetchone()["cnt"]
        trend = c.execute(
            "SELECT date, ROUND(AVG(score),2) avg_score, COUNT(*) cnt FROM ratings "
            "WHERE window_id=? AND date>=date('now','localtime','-6 days') GROUP BY date ORDER BY date",
            (wid,)).fetchall()
        feedback = c.execute(
            "SELECT p.id, p.content, p.created_at, a.nickname FROM posts p JOIN anons a ON a.id=p.anon_id "
            "WHERE p.window_id=? AND p.status=1 ORDER BY p.created_at DESC LIMIT 10", (wid,)).fetchall()
    return ok({"window": {**_window_brief(w), "avg_score": avg["avg_score"], "month_cnt": avg["cnt"],
                         "total_cnt": total},
               "dishes": [dict(d) for d in dishes],
               "trend": [dict(t) for t in trend],
               "feedback": [dict(f) for f in feedback]})


@app.route("/api/merchant/dishes", methods=["POST"])
@require_merchant
def api_merchant_add_dish():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "").strip()
    intro = (data.get("intro") or "").strip()
    try:
        price = round(float(data.get("price")), 2)
    except (TypeError, ValueError):
        return err("价格格式不正确")
    if not 1 <= len(name) <= 40:
        return err("菜名长度需在 1~40 字之间")
    if not 0 <= price <= 999:
        return err("价格超出合理范围")
    wid = session.get("uid")
    with get_db() as c:
        cnt = c.execute("SELECT COUNT(*) cnt FROM dishes WHERE window_id=? AND status=1", (wid,)).fetchone()["cnt"]
        if cnt >= 12:
            return err("在售菜品已达上限（12 道），请先下架部分菜品")
        c.execute("INSERT INTO dishes(window_id, name, price, intro, photo, status, updated_at)"
                  " VALUES (?,?,?,?,?,1,?)", (wid, name, price, intro[:200], "", now()))
        return ok({"id": c.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]})


@app.route("/api/merchant/dishes/<int:did>", methods=["PUT"])
@require_merchant
def api_merchant_update_dish(did):
    data = request.get_json(force=True, silent=True) or {}
    wid = session.get("uid")
    with get_db() as c:
        d = c.execute("SELECT * FROM dishes WHERE id=? AND window_id=?", (did, wid)).fetchone()
        if not d:
            return err("菜品不存在", 404)
        updates, args = [], []
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not 1 <= len(name) <= 40:
                return err("菜名长度需在 1~40 字之间")
            updates.append("name=?"); args.append(name)
        if "price" in data:
            try:
                price = round(float(data.get("price")), 2)
            except (TypeError, ValueError):
                return err("价格格式不正确")
            if not 0 <= price <= 999:
                return err("价格超出合理范围")
            updates.append("price=?"); args.append(price)
        if "intro" in data:
            updates.append("intro=?"); args.append((data.get("intro") or "").strip()[:200])
        if "status" in data:
            updates.append("status=?"); args.append(1 if data.get("status") else 0)
        if not updates:
            return err("没有需要更新的内容")
        updates.append("updated_at=?"); args.append(now())
        args.append(did)
        c.execute("UPDATE dishes SET %s WHERE id=?" % ",".join(updates), args)
        return ok()


@app.route("/api/merchant/dishes/<int:did>/photo", methods=["POST"])
@require_merchant
def api_merchant_upload_photo(did):
    wid = session.get("uid")
    f = request.files.get("photo")
    if not f or not f.filename:
        return err("请选择图片文件")
    ext = secure_filename(f.filename).rsplit(".", 1)[-1].lower() if "." in f.filename else ""
    if ext not in ALLOWED_PHOTO_EXT:
        return err("仅支持 png / jpg / jpeg / webp 图片")
    with get_db() as c:
        d = c.execute("SELECT * FROM dishes WHERE id=? AND window_id=?", (did, wid)).fetchone()
        if not d:
            return err("菜品不存在", 404)
        old = d["photo"]
        fname = "dish-%d-%s.%s" % (did, secrets.token_hex(4), ext)
        f.save(os.path.join(UPLOAD_DIR, fname))
        c.execute("UPDATE dishes SET photo=?, updated_at=? WHERE id=?", (fname, now(), did))
        if old and old.startswith("dish-") and os.path.exists(os.path.join(UPLOAD_DIR, old)):
            try:
                os.remove(os.path.join(UPLOAD_DIR, old))
            except OSError:
                pass
        return ok({"photo": "/uploads/" + fname})


# ---------------------------------------------------------------- 管理员端
@app.route("/api/admin/dashboard")
@require_admin
def api_admin_dashboard():
    with get_db() as c:
        month = this_month()
        window_cnt = c.execute("SELECT COUNT(*) cnt FROM windows").fetchone()["cnt"]
        active_cnt = c.execute("SELECT COUNT(*) cnt FROM windows WHERE status=1").fetchone()["cnt"]
        dish_cnt = c.execute("SELECT COUNT(*) cnt FROM dishes WHERE status=1").fetchone()["cnt"]
        month_ratings = c.execute("SELECT COUNT(*) cnt FROM ratings WHERE strftime('%Y-%m', date)=?",
                                   (month,)).fetchone()["cnt"]
        today_ratings = c.execute("SELECT COUNT(*) cnt FROM ratings WHERE date=?",
                                   (today_str(),)).fetchone()["cnt"]
        month_posts = c.execute("SELECT COUNT(*) cnt FROM posts WHERE strftime('%Y-%m', created_at)=?",
                                 (month,)).fetchone()["cnt"]
        pending = c.execute("SELECT COUNT(*) cnt FROM reports WHERE status=0").fetchone()["cnt"]
        anon_cnt = c.execute("SELECT COUNT(*) cnt FROM anons").fetchone()["cnt"]
        top5 = c.execute(
            "SELECT w.id, w.name, w.canteen, w.floor, w.win_no, ROUND(AVG(r.score),2) avg_score, COUNT(*) cnt "
            "FROM ratings r JOIN windows w ON w.id=r.window_id WHERE strftime('%Y-%m', r.date)=? "
            "GROUP BY w.id ORDER BY avg_score DESC LIMIT 5", (month,)).fetchall()
    return ok({"month": month, "window_cnt": window_cnt, "active_cnt": active_cnt,
               "dish_cnt": dish_cnt, "month_ratings": month_ratings,
               "today_ratings": today_ratings, "month_posts": month_posts,
               "pending_reports": pending, "anon_cnt": anon_cnt,
               "top5": [dict(t) for t in top5]})


@app.route("/api/admin/windows")
@require_admin
def api_admin_windows():
    with get_db() as c:
        rows = c.execute(
            "SELECT w.*, (SELECT COUNT(*) FROM dishes d WHERE d.window_id=w.id AND d.status=1) dish_cnt, "
            "(SELECT COUNT(*) FROM ratings r WHERE r.window_id=w.id AND strftime('%Y-%m', r.date)=?) month_cnt "
            "FROM windows w ORDER BY w.canteen, w.floor, w.win_no", (this_month(),)).fetchall()
    return ok({"windows": [dict(r) for r in rows]})


@app.route("/api/admin/windows/<int:wid>", methods=["PUT"])
@require_admin
def api_admin_update_window(wid):
    data = request.get_json(force=True, silent=True) or {}
    with get_db() as c:
        w = c.execute("SELECT * FROM windows WHERE id=?", (wid,)).fetchone()
        if not w:
            return err("窗口不存在", 404)
        updates, args = [], []
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not 1 <= len(name) <= 30:
                return err("窗口名长度需在 1~30 字之间")
            updates.append("name=?"); args.append(name)
        if "merchant_name" in data:
            m = (data.get("merchant_name") or "").strip()
            if not 1 <= len(m) <= 30:
                return err("商家名长度需在 1~30 字之间")
            updates.append("merchant_name=?"); args.append(m)
        if "status" in data:
            updates.append("status=?"); args.append(1 if data.get("status") else 0)
        if not updates:
            return err("没有需要更新的内容")
        args.append(wid)
        c.execute("UPDATE windows SET %s WHERE id=?" % ",".join(updates), args)
        admin_log(c, "窗口信息变更",
                  "窗口 c%d-%d-%02d：%s" % (w["canteen"], w["floor"], w["win_no"],
                                            "、".join("%s=%s" % (u.split("=")[0], a if not isinstance(a, int) else a)
                                                      for u, a in zip(updates, args[:-1]))))
        return ok()


@app.route("/api/admin/windows/<int:wid>/reset-merchant-password", methods=["POST"])
@require_admin
def api_admin_reset_merchant_pw(wid):
    alphabet = string.ascii_letters + string.digits
    new_pw = "".join(secrets.choice(alphabet) for _ in range(8))
    with get_db() as c:
        w = c.execute("SELECT * FROM windows WHERE id=?", (wid,)).fetchone()
        if not w:
            return err("窗口不存在", 404)
        c.execute("UPDATE windows SET merchant_pass_hash=? WHERE id=?",
                  (generate_password_hash(new_pw), wid))
        admin_log(c, "重置商家密码", "窗口 %s（%s）密码已重置" % (w["merchant_user"], w["name"]))
        return ok({"username": w["merchant_user"], "password": new_pw})


def _admin_post_list(where, args):
    with get_db() as c:
        rows = c.execute(
            "SELECT p.*, a.nickname, w.name AS window_name, w.canteen, w.floor, w.win_no, "
            "(SELECT COUNT(*) FROM post_likes l WHERE l.post_id=p.id) likes, "
            "(SELECT COUNT(*) FROM reports rp WHERE rp.post_id=p.id AND rp.status=0) pending_reports "
            "FROM posts p JOIN anons a ON a.id=p.anon_id JOIN windows w ON w.id=p.window_id "
            + where + " ORDER BY p.created_at DESC LIMIT 300", args).fetchall()
        return [dict(r) for r in rows]


@app.route("/api/admin/posts")
@require_admin
def api_admin_posts():
    status = request.args.get("status")
    if status in ("1", "0"):
        return ok({"posts": _admin_post_list("WHERE p.status=?", [int(status)])})
    return ok({"posts": _admin_post_list("", [])})


def _soft_delete_post(conn, pid, note):
    """软删主帖并级联隐藏其回帖"""
    p = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
    if not p:
        return False
    conn.execute("UPDATE posts SET status=0 WHERE id=?", (pid,))
    conn.execute("UPDATE posts SET status=0 WHERE parent_id=?", (pid,))
    w = conn.execute("SELECT name FROM windows WHERE id=?", (p["window_id"],)).fetchone()
    admin_log(conn, "删除违规帖子", "「%s」：%s（仅显示前50字）" % (w["name"], (p["content"] or "")[:50]))
    return True


@app.route("/api/admin/posts/<int:pid>", methods=["DELETE"])
@require_admin
def api_admin_delete_post(pid):
    with get_db() as c:
        if not _soft_delete_post(c, pid, "管理员删除"):
            return err("帖子不存在", 404)
        c.execute("UPDATE reports SET status=1 WHERE post_id=? AND status=0", (pid,))
        return ok()


@app.route("/api/admin/posts/<int:pid>/restore", methods=["POST"])
@require_admin
def api_admin_restore_post(pid):
    with get_db() as c:
        p = c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        if not p:
            return err("帖子不存在", 404)
        c.execute("UPDATE posts SET status=1 WHERE id=?", (pid,))
        if p["parent_id"] is None:
            c.execute("UPDATE posts SET status=1 WHERE parent_id=?", (pid,))
        else:  # 恢复回帖时，确保其主帖可见
            c.execute("UPDATE posts SET status=1 WHERE id=?", (p["parent_id"],))
        admin_log(c, "恢复帖子", "帖子 #%s 已恢复" % pid)
        return ok()


@app.route("/api/admin/reports")
@require_admin
def api_admin_reports():
    status = request.args.get("status", "0")
    with get_db() as c:
        rows = c.execute(
            "SELECT rp.id AS report_id, rp.reason, rp.created_at AS report_time, rp.status, "
            "p.id AS post_id, p.content, p.status AS post_status, a.nickname, "
            "w.name AS window_name, w.id AS window_id "
            "FROM reports rp JOIN posts p ON p.id=rp.post_id JOIN anons a ON a.id=rp.anon_id "
            "JOIN windows w ON w.id=p.window_id WHERE rp.status=? ORDER BY rp.created_at DESC LIMIT 200",
            (int(status),)).fetchall()
        return ok({"reports": [dict(r) for r in rows]})


@app.route("/api/admin/reports/<int:rid>/resolve", methods=["POST"])
@require_admin
def api_admin_resolve_report(rid):
    data = request.get_json(force=True, silent=True) or {}
    action = data.get("action")
    if action not in ("delete", "dismiss"):
        return err("处理动作不合法")
    with get_db() as c:
        rp = c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone()
        if not rp or rp["status"] != 0:
            return err("举报不存在或已处理", 404)
        if action == "delete":
            _soft_delete_post(c, rp["post_id"], "举报属实删除")
            c.execute("UPDATE reports SET status=1 WHERE id=?", (rid,))
            admin_log(c, "举报处理-删帖", "举报 #%s 属实，已删除帖子 #%s" % (rid, rp["post_id"]))
        else:
            c.execute("UPDATE reports SET status=2 WHERE id=?", (rid,))
            admin_log(c, "举报处理-驳回", "举报 #%s 不属实，已驳回" % rid)
        return ok()


@app.route("/api/admin/logs")
@require_admin
def api_admin_logs():
    with get_db() as c:
        rows = c.execute("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 100").fetchall()
        return ok({"logs": [dict(r) for r in rows]})


@app.route("/api/admin/change-password", methods=["POST"])
@require_admin
def api_admin_change_password():
    data = request.get_json(force=True, silent=True) or {}
    old = data.get("old") or ""
    new = data.get("new") or ""
    if len(new) < 8:
        return err("新密码至少 8 位")
    with get_db() as c:
        a = c.execute("SELECT * FROM admins WHERE id=?", (session.get("uid"),)).fetchone()
        if not a or not check_password_hash(a["password_hash"], old):
            return err("原密码不正确", 401)
        c.execute("UPDATE admins SET password_hash=? WHERE id=?", (generate_password_hash(new), a["id"]))
        admin_log(c, "修改管理员密码", "管理员 %s 修改了登录密码" % a["username"])
        return ok()


# ---------------------------------------------------------------- 启动
def run():
    database.init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5001"))
    if not os.environ.get("ADMIN_PASSWORD"):
        print("⚠  安全提醒：当前管理员使用默认口令 admin@2026。公网部署前请通过环境变量 "
              "ADMIN_PASSWORD 修改为强口令，或登录后台在「修改密码」里改掉。")
    print("食堂匿名打分系统启动: http://%s:%d" % (host, port))
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run()
