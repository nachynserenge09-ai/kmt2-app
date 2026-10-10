import hashlib, hmac, json, os, sqlite3, time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "kmt.db"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")   # токен от BotFather
DEMO = os.getenv("DEMO", "1") == "1"     # демо-оценки при регистрации

app = FastAPI()

# Расписание звонков. ЗАМЕНИ на реальное время вашего колледжа.
BELLS = [
    {"n": 1, "start": "08:30", "end": "10:00"},
    {"n": 2, "start": "10:10", "end": "11:40"},
    {"n": 3, "start": "12:10", "end": "13:40"},
    {"n": 4, "start": "13:50", "end": "15:20"},
    {"n": 5, "start": "15:30", "end": "17:00"},
]

# ---------- база данных ----------
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS students(
        telegram_id INTEGER PRIMARY KEY, name TEXT, group_name TEXT);
    CREATE TABLE IF NOT EXISTS schedule(
        id INTEGER PRIMARY KEY, group_name TEXT, weekday INTEGER,
        num INTEGER, subject TEXT, room TEXT, teacher TEXT);
    CREATE TABLE IF NOT EXISTS grades(
        id INTEGER PRIMARY KEY, telegram_id INTEGER, subject TEXT,
        grade INTEGER, date TEXT);
    """)
    if con.execute("SELECT COUNT(*) FROM schedule").fetchone()[0] == 0:
        demo = [  # группа, день (0=пн), пара, предмет, кабинет, преподаватель
            ("1-ИС", 0, 1, "Математика", "201", "Иванова А.П."),
            ("1-ИС", 0, 2, "Информатика", "312", "Петров С.И."),
            ("1-ИС", 0, 3, "Физкультура", "Зал", "Сидоров В.Н."),
            ("1-ИС", 1, 1, "Русский язык", "105", "Кузнецова О.М."),
            ("1-ИС", 1, 2, "Математика", "201", "Иванова А.П."),
            ("1-ИС", 2, 2, "Информатика", "312", "Петров С.И."),
            ("1-ИС", 2, 3, "История", "118", "Громов Д.А."),
            ("1-ИС", 3, 1, "Английский язык", "220", "Волкова Е.С."),
            ("1-ИС", 4, 1, "Программирование", "314", "Петров С.И."),
            ("1-ИС", 4, 2, "Программирование", "314", "Петров С.И."),
        ]
        con.executemany(
            "INSERT INTO schedule(group_name,weekday,num,subject,room,teacher) VALUES(?,?,?,?,?,?)", demo)
    con.commit(); con.close()

init_db()

# ---------- проверка подписи Telegram ----------
def get_user(init_data: str) -> dict:
    """Возвращает данные пользователя из initData или кидает 401."""
    if not BOT_TOKEN:  # режим разработки: без токена проверки нет
        return {"id": 1, "first_name": "Тест"}
    try:
        data = dict(parse_qsl(init_data, strict_parsing=True))
        received_hash = data.pop("hash")
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, received_hash):
            raise ValueError("bad hash")
        if time.time() - int(data["auth_date"]) > 86400:
            raise ValueError("expired")
        return json.loads(data["user"])
    except Exception:
        raise HTTPException(401, "Неверные данные Telegram")

# ---------- API ----------
@app.get("/api/me")
def me(x_init_data: str = Header(default="")):
    user = get_user(x_init_data)
    con = db()
    s = con.execute("SELECT * FROM students WHERE telegram_id=?", (user["id"],)).fetchone()
    groups = [r[0] for r in con.execute("SELECT DISTINCT group_name FROM schedule")]
    con.close()
    return {"registered": bool(s), "name": s["name"] if s else user.get("first_name", ""),
            "group": s["group_name"] if s else None, "groups": groups}

class Reg(BaseModel):
    name: str
    group: str

@app.post("/api/register")
def register(r: Reg, x_init_data: str = Header(default="")):
    user = get_user(x_init_data)
    con = db()
    con.execute("INSERT OR REPLACE INTO students VALUES(?,?,?)",
                (user["id"], r.name.strip()[:60], r.group))
    if DEMO and not con.execute("SELECT 1 FROM grades WHERE telegram_id=?", (user["id"],)).fetchone():
        subs = [x[0] for x in con.execute(
            "SELECT DISTINCT subject FROM schedule WHERE group_name=?", (r.group,))]
        vals = [(user["id"], sub, g, f"2026-10-0{i+1}")
                for sub in subs for i, g in enumerate([5, 4, 4])]
        con.executemany("INSERT INTO grades(telegram_id,subject,grade,date) VALUES(?,?,?,?)", vals)
    con.commit(); con.close()
    return {"ok": True}

@app.get("/api/schedule")
def schedule(x_init_data: str = Header(default="")):
    user = get_user(x_init_data)
    con = db()
    s = con.execute("SELECT group_name FROM students WHERE telegram_id=?", (user["id"],)).fetchone()
    if not s:
        raise HTTPException(404, "Сначала зарегистрируйтесь")
    rows = con.execute("SELECT weekday,num,subject,room,teacher FROM schedule "
                       "WHERE group_name=? ORDER BY weekday,num", (s["group_name"],)).fetchall()
    con.close()
    return {"days": [[dict(r) for r in rows if r["weekday"] == d] for d in range(6)], "bells": BELLS}

@app.get("/api/grades")
def grades(x_init_data: str = Header(default="")):
    user = get_user(x_init_data)
    con = db()
    rows = con.execute("SELECT subject,grade,date FROM grades WHERE telegram_id=? ORDER BY date",
                       (user["id"],)).fetchall()
    con.close()
    by = {}
    for r in rows:
        by.setdefault(r["subject"], []).append(r["grade"])
    items = [{"subject": k, "grades": v, "avg": round(sum(v) / len(v), 2)} for k, v in by.items()]
    allg = [g for v in by.values() for g in v]
    return {"items": items, "gpa": round(sum(allg) / len(allg), 2) if allg else None}

@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE_DIR / "index.html").read_text(encoding="utf-8")
