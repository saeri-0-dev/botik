#bot-token: 8660115424:AAGn_h5EMSnAmzhJKDe-DxSeJjPtE1IkJYY
#chat-id: -1002969008348
#theme-general: 5
#theme-homework: 8
import asyncio
import os
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import json
from pathlib import Path
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest

from apscheduler.schedulers.asyncio import AsyncIOScheduler

def env_required(name: str) -> str:
    v = os.getenv(name)
    if not v or not v.strip():
        raise RuntimeError(f"Нужна переменная окружения {name}")
    return v.strip()

def env_int_required(name: str) -> int:
    v = env_required(name)
    try:
        return int(v)
    except:
        raise RuntimeError(f"{name} должно быть числом (int)")

TOKEN = env_required("BOT_TOKEN")  # БЕЗ дефолта
PASS = os.getenv("LOGIN_PASSWORD", "").strip()  # можно пустым (тогда логин только по админам)

ADMINS_RAW = os.getenv("ADMIN_USERNAMES", "")
ADMIN_UNS = {x.strip().lstrip("@").lower() for x in ADMINS_RAW.split(",") if x.strip()}

TZ_NAME = os.getenv("TZ", "Europe/Moscow")
TZ = ZoneInfo(TZ_NAME)

HW_CHAT_ID = -1003714586762  # статично
HW_THREAD_ID = int(os.getenv("HW_THREAD_ID", "0"))  # можно оставить env, или тоже зафиксировать

DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "db.json"


router = Router()

aut = set()  
wl = []
st = {}  


MAX_TG = 4096

def split_tg(s: str, lim: int = MAX_TG) -> list[str]:
    s = s or ""
    if len(s) <= lim:
        return [s]
    a = []
    i = 0
    n = len(s)
    while i < n:
        j = min(i + lim, n)
        k = s.rfind("\n", i, j)
        if k <= i + 50:
            k = j
        a.append(s[i:k])
        i = k
    return a

async def send_long(bot: Bot, chat_id: int, text: str, thread_id: int = 0) -> int:
    parts = split_tg(text, MAX_TG)
    first_id = 0
    for idx, p in enumerate(parts):
        if thread_id:
            m = await bot.send_message(chat_id, p, message_thread_id=thread_id)
        else:
            m = await bot.send_message(chat_id, p)
        if idx == 0:
            first_id = m.message_id
    return first_id


# ---------- helpers ----------

def is_private(m: Message) -> bool:
    return m.chat.type == "private"

def need_login(x) -> bool:
    return x.from_user.id not in aut

def is_admin_user(u) -> bool:
    if not u or not u.username:
        return False
    return u.username.lower() in ADMIN_UNS

def st_set(uid: int, **kw):
    st.setdefault(uid, {}).update(kw)

def st_get(uid: int, k: str, d=None):
    return st.get(uid, {}).get(k, d)

def st_clear(uid: int):
    st.pop(uid, None)

def fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


# ---------- wl file ----------

def read_wl() -> list[str]:
    p = os.path.join(os.path.dirname(__file__), "ids.txt")
    if not os.path.exists(p):
        return []
    a = []
    seen = set()
    with open(p, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s[0] != "@":
                s = "@" + s
            if s not in seen:
                seen.add(s)
                a.append(s)
    return a

def mentions_text() -> str:
    if not wl:
        return "\n\n(белый список пуст)"
    return "\n\nУпоминание:\n" + " ".join(wl)


# ---------- JSON "DB" ----------

_db_lock = asyncio.Lock()

def _db_default():
    return {
        "next_hw_id": 1,
        "next_hh_id": 1,
        "next_ht_id": 1,
        "hw": [],  # list of dict
        "hh": [],  # list of dict
        "ht": [],  # list of dict
    }

async def db_init():
    async with _db_lock:
        if not DB_PATH.exists():
            tmp = DB_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(_db_default(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(DB_PATH)
            return

        # если файл битый — не падаем молча
        try:
            json.loads(DB_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"db.json повреждён: {e}")

def _load_db_unsafe() -> dict:
    if not DB_PATH.exists():
        return _db_default()
    return json.loads(DB_PATH.read_text(encoding="utf-8"))

def _save_db_unsafe(db: dict):
    tmp = DB_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_PATH)

# ----- обычное дз -----

async def hw_add(dt_iso: str, subj: str, kind: str, txt: str, fid: str, mode: int):
    async with _db_lock:
        db = _load_db_unsafe()
        hw_id = db["next_hw_id"]
        db["next_hw_id"] += 1
        db["hw"].append({
            "id": hw_id,
            "dt": dt_iso,
            "subj": subj,
            "kind": kind,
            "txt": txt or "",
            "fid": fid or "",
            "mode": int(mode),
            "r24": 0,
            "r10": 0,
            "done": 0,
        })
        _save_db_unsafe(db)

async def hw_list_active():
    async with _db_lock:
        db = _load_db_unsafe()
        out = []
        for row in db["hw"]:
            if int(row.get("done", 0)) == 0:
                out.append((
                    int(row["id"]),
                    row["dt"],
                    row["subj"],
                    row["kind"],
                    row.get("txt", ""),
                    row.get("fid", ""),
                    int(row.get("mode", 1)),
                    int(row.get("r24", 0)),
                    int(row.get("r10", 0)),
                ))
        return out

async def hw_set_flags(i: int, r24=None, r10=None, done=None):
    async with _db_lock:
        db = _load_db_unsafe()
        for row in db["hw"]:
            if int(row["id"]) == int(i):
                if r24 is not None: row["r24"] = int(r24)
                if r10 is not None: row["r10"] = int(r10)
                if done is not None: row["done"] = int(done)
                break
        _save_db_unsafe(db)

# ----- история -----

async def hh_add(dt_iso: str) -> int:
    async with _db_lock:
        db = _load_db_unsafe()
        hid = db["next_hh_id"]
        db["next_hh_id"] += 1
        db["hh"].append({
            "id": hid,
            "dt": dt_iso,
            "msg_id": 0,
            "r24": 0,
            "r10": 0,
            "done": 0,
        })
        _save_db_unsafe(db)
        return hid

async def hh_set_msg(hid: int, msg_id: int):
    async with _db_lock:
        db = _load_db_unsafe()
        for row in db["hh"]:
            if int(row["id"]) == int(hid):
                row["msg_id"] = int(msg_id)
                break
        _save_db_unsafe(db)

async def hh_list():
    async with _db_lock:
        db = _load_db_unsafe()
        rows = []
        for row in db["hh"]:
            rows.append((
                int(row["id"]),
                row["dt"],
                int(row.get("msg_id", 0)),
                int(row.get("r24", 0)),
                int(row.get("r10", 0)),
                int(row.get("done", 0)),
            ))
        rows.sort(key=lambda x: x[0], reverse=True)
        return rows

async def hh_get(hid: int):
    async with _db_lock:
        db = _load_db_unsafe()
        for row in db["hh"]:
            if int(row["id"]) == int(hid):
                return (
                    int(row["id"]),
                    row["dt"],
                    int(row.get("msg_id", 0)),
                    int(row.get("r24", 0)),
                    int(row.get("r10", 0)),
                    int(row.get("done", 0)),
                )
        return None

async def hh_set_flags(hid: int, r24=None, r10=None, done=None):
    async with _db_lock:
        db = _load_db_unsafe()
        for row in db["hh"]:
            if int(row["id"]) == int(hid):
                if r24 is not None: row["r24"] = int(r24)
                if r10 is not None: row["r10"] = int(r10)
                if done is not None: row["done"] = int(done)
                break
        _save_db_unsafe(db)

async def hh_del(hid: int):
    async with _db_lock:
        db = _load_db_unsafe()
        hid = int(hid)
        db["ht"] = [t for t in db["ht"] if int(t["hid"]) != hid]
        db["hh"] = [h for h in db["hh"] if int(h["id"]) != hid]
        _save_db_unsafe(db)

async def ht_add(hid: int, sem: int, title: str):
    async with _db_lock:
        db = _load_db_unsafe()
        tid = db["next_ht_id"]
        db["next_ht_id"] += 1
        db["ht"].append({
            "id": tid,
            "hid": int(hid),
            "sem": int(sem),
            "title": title,
            "who": "",
        })
        _save_db_unsafe(db)

async def ht_list(hid: int):
    async with _db_lock:
        db = _load_db_unsafe()
        hid = int(hid)
        rows = []
        for t in db["ht"]:
            if int(t["hid"]) == hid:
                rows.append((int(t["id"]), int(t["sem"]), t["title"], t.get("who", "")))
        rows.sort(key=lambda x: (x[1], x[0]))
        return rows

async def ht_set_who(tid: int, who: str):
    async with _db_lock:
        db = _load_db_unsafe()
        for t in db["ht"]:
            if int(t["id"]) == int(tid):
                t["who"] = who or ""
                break
        _save_db_unsafe(db)

async def ht_set_title(tid: int, title: str):
    async with _db_lock:
        db = _load_db_unsafe()
        for t in db["ht"]:
            if int(t["id"]) == int(tid):
                t["title"] = title
                break
        _save_db_unsafe(db)

# ---------- send helpers ----------

async def send_hw_text(bot: Bot, text: str):
    await bot.send_message(HW_CHAT_ID, text, message_thread_id=HW_THREAD_ID)

async def send_hw_item(bot: Bot, dt: datetime, subj: str, kind: str, txt: str, fid: str):
    head = f"📌 ДЗ\n🗓 {fmt_dt(dt)}\n📚 {subj}\n"

    if kind == "text":
        await send_hw_text(bot, head + "\n" + txt + mentions_text())
        return

    cap = head
    if txt:
        cap += "\n" + txt

    if kind == "photo":
        await bot.send_photo(HW_CHAT_ID, fid, caption=cap, message_thread_id=HW_THREAD_ID)
    elif kind == "doc":
        await bot.send_document(HW_CHAT_ID, fid, caption=cap, message_thread_id=HW_THREAD_ID)
    elif kind == "video":
        await bot.send_video(HW_CHAT_ID, fid, caption=cap, message_thread_id=HW_THREAD_ID)
    elif kind == "audio":
        await bot.send_audio(HW_CHAT_ID, fid, caption=cap, message_thread_id=HW_THREAD_ID)
    else:
        await send_hw_text(bot, head + "\n(неизвестный тип)")

    if wl:
        await send_hw_text(bot, "Упоминание:\n" + " ".join(wl))


def hh_text(dt: datetime, ts) -> str:
    s = "📚 ДЗ ИСТОРИЯ\n"
    s += f"🗓 {fmt_dt(dt)}\n\n"
    cur = -1
    for _, sem, title, who in ts:
        if sem != cur:
            cur = sem
            s += f"Семинар {sem}:\n"
        w = who if who else "не выбрано"
        s += f"— {title} — {w}\n"
    return s + mentions_text()


# ---------- Keyboards ----------

def kb_main():
    kb = InlineKeyboardBuilder()
    kb.button(text="📋 БЕЛЫЙ СПИСОК", callback_data="wl_0")
    kb.button(text="📣 Уведомление", callback_data="nt")
    kb.button(text="📤 Отправка ДЗ", callback_data="hw_menu")
    kb.adjust(1)
    return kb.as_markup()

def kb_back_to_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()

def kb_cancel():
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()

def kb_wl(page: int, tp: int):
    kb = InlineKeyboardBuilder()
    prev_p = page - 1
    next_p = page + 1

    kb.button(text="◀️", callback_data=f"wl_{prev_p}" if prev_p >= 0 else "wl_no")
    kb.button(text=f"{page+1}/{max(1,tp)}", callback_data="wl_no")
    kb.button(text="▶️", callback_data=f"wl_{next_p}" if next_p < tp else "wl_no")

    kb.button(text="🔄 Обновить", callback_data=f"wl_refresh_{page}")
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(3, 1, 1)
    return kb.as_markup()

def kb_hw_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Обычное ДЗ", callback_data="hw_norm")
    kb.button(text="📚 ДЗ История", callback_data="hw_hist")
    kb.button(text="📚 История: записи", callback_data="hh_list_0")
    kb.button(text="⬅️ Назад", callback_data="menu")
    kb.adjust(1)
    return kb.as_markup()

def kb_hw_mode():
    kb = InlineKeyboardBuilder()
    kb.button(text="🚀 Отправить сразу + напоминать", callback_data="hwm_1")
    kb.button(text="⏰ Только напоминать", callback_data="hwm_2")
    kb.button(text="⬅️ Назад", callback_data="hw_menu")
    kb.adjust(1)
    return kb.as_markup()

def kb_hh_actions(hid: int):
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Назначения", callback_data=f"hh_as_{hid}")
    kb.button(text="📝 Переименовать тему", callback_data=f"hh_ren_{hid}")
    kb.button(text="🎲 Рандом оставшихся", callback_data=f"hh_rand_{hid}")
    kb.button(text="✅ Изменить старое сообщение", callback_data=f"hh_edit_{hid}")
    kb.button(text="➕ Отправить новое сообщение", callback_data=f"hh_new_{hid}")
    kb.button(text="🗑 Удалить", callback_data=f"hh_del_{hid}")
    kb.button(text="⬅️ Назад", callback_data="hh_list_0")
    kb.adjust(1)
    return kb.as_markup()

def kb_hh_list(rows, page: int):
    per = 5
    n = len(rows)
    tp = (n + per - 1) // per if n else 1
    if page < 0: page = 0
    if page >= tp: page = tp - 1

    l = page * per
    r = min(l + per, n)

    kb = InlineKeyboardBuilder()
    for i in range(l, r):
        hid, dt_s, msg_id, r24, r10, done = rows[i]
        dt = datetime.fromisoformat(dt_s)
        kb.button(text=f"{hid}) {fmt_dt(dt)}", callback_data=f"hh_open_{hid}")

    kb.button(text="◀️", callback_data=f"hh_list_{page-1}" if page > 0 else "hh_no")
    kb.button(text=f"{page+1}/{max(1,tp)}", callback_data="hh_no")
    kb.button(text="▶️", callback_data=f"hh_list_{page+1}" if page+1 < tp else "hh_no")

    kb.button(text="⬅️ Назад", callback_data="hw_menu")
    kb.adjust(1, 3, 1)
    return kb.as_markup()


# ---------- whitelist render ----------

async def render_wl(page: int) -> tuple[str, int, int]:
    per = 5
    n = len(wl)
    tp = (n + per - 1) // per if n else 1
    if page < 0: page = 0
    if page >= tp: page = tp - 1

    l = page * per
    r = min(l + per, n)

    txt = "📋 БЕЛЫЙ СПИСОК (ids.txt)\n"
    txt += f"Всего: {n}\n\n"
    if n == 0:
        txt += "Файл ids.txt пуст или не найден.\nПиши @username по одной строке."
        return txt, page, tp

    for i in range(l, r):
        txt += f"{i+1}) {wl[i]}\n"
    return txt, page, tp


# ---------- notify send ----------

async def send_to_target(bot: Bot, cid: int, tid: int, text: str):
    if tid and tid != 0:
        await bot.send_message(cid, text, message_thread_id=tid)
    else:
        await bot.send_message(cid, text)


# ==================== COMMANDS ====================

@router.message(CommandStart())
async def start(m: Message):
    # отвечаем только в личке
    if not is_private(m):
        return
    await m.answer("Вход: /login пароль")

@router.message(Command("menu"))
async def menu_cmd(m: Message):
    if not is_private(m):
        return
    if need_login(m):
        await m.answer("Сначала /login пароль")
        return
    st_clear(m.from_user.id)
    await m.answer("Меню:", reply_markup=kb_main())

@router.message(Command("login"))
async def login(m: Message):
    if not is_private(m):
        return

    # автологин по username
    if is_admin_user(m.from_user):
        aut.add(m.from_user.id)
        await m.answer("Ок (admin). Меню:", reply_markup=kb_main())
        return

    t = (m.text or "").split(maxsplit=1)
    if len(t) < 2:
        await m.answer("Пиши так: /login 123")
        return
    if t[1].strip() == PASS:
        aut.add(m.from_user.id)
        await m.answer("Ок. Меню:", reply_markup=kb_main())
    else:
        await m.answer("Неверный пароль.")


# ==================== CALLBACKS ====================

@router.callback_query(F.data == "menu")
async def menu_cb(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    st_clear(cb.from_user.id)
    await cb.message.edit_text("Меню:", reply_markup=kb_main())
    await cb.answer()


# ----- whitelist -----

@router.callback_query(F.data == "wl_no")
async def wl_no(cb: CallbackQuery):
    await cb.answer()

@router.callback_query(F.data.startswith("wl_refresh_"))
async def wl_refresh(cb: CallbackQuery):
    global wl
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    
    wl = read_wl()
    page = int(cb.data.split("_")[-1])
    txt, page, tp = await render_wl(page)
    try:
        await cb.message.edit_text(txt, reply_markup=kb_wl(page, tp))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await cb.answer("Обновлено")

@router.callback_query(F.data.startswith("wl_"))
async def wl_show(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    page = int(cb.data.split("_")[1])
    txt, page, tp = await render_wl(page)
    try:
        await cb.message.edit_text(txt, reply_markup=kb_wl(page, tp))
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e):
            raise
    await cb.answer()


# ----- main menu buttons -----

@router.callback_query(F.data == "hw_menu")
async def hw_menu(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    await cb.message.edit_text("Отправка ДЗ:", reply_markup=kb_hw_menu())
    await cb.answer()


@router.callback_query(F.data == "nt")
async def nt(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    st_set(cb.from_user.id, mode="nt_where")
    await cb.message.edit_text(
        "Уведомление.\nВведи chat_id и thread_id через пробел.\nПример: -100... 2\nЕсли не тема: thread_id = 0",
        reply_markup=kb_cancel()
    )
    await cb.answer()


# ----- обычное дз -----

@router.callback_query(F.data == "hw_norm")
async def hw_norm(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    st_set(cb.from_user.id, mode="hw_mode")
    await cb.message.edit_text("Обычное ДЗ: выбери режим.", reply_markup=kb_hw_mode())
    await cb.answer()

@router.callback_query(F.data.startswith("hwm_"))
async def hw_mode_pick(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    mode = int(cb.data.split("_")[1])  # 1 или 2
    st_set(cb.from_user.id, mode="hw_dt", hw_mode=mode)
    await cb.message.edit_text(
        "Введи дату и время пары: YYYY-MM-DD HH:MM\nПример: 2026-03-01 09:00",
        reply_markup=kb_cancel()
    )
    await cb.answer()


# ----- история -----

@router.callback_query(F.data == "hw_hist")
async def hw_hist(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    st_set(cb.from_user.id, mode="hh_dt")
    await cb.message.edit_text(
        "История: введи дату и время пары: YYYY-MM-DD HH:MM\nПример: 2026-03-01 09:00",
        reply_markup=kb_cancel()
    )
    await cb.answer()


@router.callback_query(F.data == "hh_no")
async def hh_no(cb: CallbackQuery):
    await cb.answer()

@router.callback_query(F.data.startswith("hh_list_"))
async def hh_list_cb(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    page = int(cb.data.split("_")[2])
    rows = await hh_list()
    txt = "📚 История: записи\n"
    if not rows:
        txt += "\n(пока нет записей)"
    await cb.message.edit_text(txt, reply_markup=kb_hh_list(rows, page))
    await cb.answer()

@router.callback_query(F.data.startswith("hh_open_"))
async def hh_open(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    hid = int(cb.data.split("_")[2])
    r = await hh_get(hid)
    if not r:
        await cb.answer("Нет записи", show_alert=True)
        return
    _, dt_s, msg_id, *_ = r
    dt = datetime.fromisoformat(dt_s)
    ts = await ht_list(hid)
    txt = hh_text(dt, ts) + f"\n\nid: {hid}\nmsg_id: {msg_id}"
    await cb.message.edit_text(txt, reply_markup=kb_hh_actions(hid))
    await cb.answer()

@router.callback_query(F.data.startswith("hh_del_"))
async def hh_del_cb(cb: CallbackQuery, bot: Bot):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    hid = int(cb.data.split("_")[2])
    r = await hh_get(hid)
    if not r:
        await cb.answer("Нет записи", show_alert=True)
        return
    _, _, msg_id, *_ = r
    if msg_id:
        try:
            await bot.delete_message(HW_CHAT_ID, msg_id)
        except:
            pass
    await hh_del(hid)
    await cb.answer("Удалено")
    rows = await hh_list()
    await cb.message.edit_text("📚 История: записи", reply_markup=kb_hh_list(rows, 0))

@router.callback_query(F.data.startswith("hh_as_"))
async def hh_as(cb: CallbackQuery, bot: Bot):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    hid = int(cb.data.split("_")[2])
    st_set(cb.from_user.id, mode="hh_assign", hid=hid)
    await cb.message.edit_text(
        "Режим назначений.\n"
        "Команда: тема_номер человек_номер\n"
        "Пример: 3 1\n"
        "Снять: 3 0\n"
        "list — показать списки\n"
        "stop — закончить (не отправляя)\n"
        "send — отправить/обновить сообщение в теме ДЗ",
        reply_markup=kb_cancel()
    )
    await cb.answer()
    await show_assign_lists(bot, cb.message.chat.id, hid)

@router.callback_query(F.data.startswith("hh_ren_"))
async def hh_ren(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    hid = int(cb.data.split("_")[2])
    st_set(cb.from_user.id, mode="hh_rename", hid=hid)
    await cb.message.edit_text(
        "Переименование.\nПиши: тема_номер новый_текст\nПример: 2 Русско-японская война",
        reply_markup=kb_cancel()
    )
    await cb.answer()

@router.callback_query(F.data.startswith("hh_rand_"))
async def hh_rand(cb: CallbackQuery):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    hid = int(cb.data.split("_")[2])
    st_set(cb.from_user.id, mode="hh_rand_ex", hid=hid)
    await cb.message.edit_text(
        "Рандом оставшихся.\n"
        "Напиши номера людей (из ids.txt) которых НЕ учитывать, через пробел.\n"
        "Если никого — 0.\nПример: 2 5",
        reply_markup=kb_cancel()
    )
    await cb.answer()

@router.callback_query(F.data.startswith("hh_edit_"))
async def hh_edit(cb: CallbackQuery, bot: Bot):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return

    hid = int(cb.data.split("_")[2])
    r = await hh_get(hid)
    if not r:
        await cb.answer("Нет записи", show_alert=True)
        return

    _, dt_s, msg_id, *_ = r
    dt = datetime.fromisoformat(dt_s)
    ts = await ht_list(hid)
    txt = hh_text(dt, ts)


    if msg_id:
        try:
            await bot.delete_message(HW_CHAT_ID, msg_id)
        except:
            pass


    try:
        new_id = await send_long(bot, HW_CHAT_ID, txt, HW_THREAD_ID)
    except Exception as e:
        await cb.answer("Не смог отправить (текст слишком длинный или нет прав)", show_alert=True)
        return

    await hh_set_msg(hid, new_id)
    await cb.answer("Заменил ✅")


    await hh_open(cb)

@router.callback_query(F.data.startswith("hh_new_"))
async def hh_new(cb: CallbackQuery, bot: Bot):
    if cb.message.chat.type != "private":
        await cb.answer()
        return
    if need_login(cb):
        await cb.answer("Сначала /login пароль", show_alert=True)
        return
    hid = int(cb.data.split("_")[2])
    r = await hh_get(hid)
    if not r:
        await cb.answer("Нет записи", show_alert=True)
        return
    _, dt_s, _, *_ = r
    dt = datetime.fromisoformat(dt_s)
    ts = await ht_list(hid)
    txt = hh_text(dt, ts)
    m = await bot.send_message(HW_CHAT_ID, txt, message_thread_id=HW_THREAD_ID)
    await hh_set_msg(hid, m.message_id)
    await cb.answer("Отправил новое")
    await hh_open(cb)


# ==================== MESSAGE FLOW (PRIVATE ONLY) ====================

async def show_assign_lists(bot: Bot, chat_id: int, hid: int):
    global wl
    wl = read_wl()
    ts = await ht_list(hid)

    txt = "📌 Темы:\n"
    for i, (tid, sem, title, who) in enumerate(ts, start=1):
        w = who if who else "не выбрано"
        txt += f"{i}) [С{sem}] {title} — {w}\n"

    txt += "\n👥 Люди (ids.txt):\n"
    for i, u in enumerate(wl, start=1):
        txt += f"{i}) {u}\n"

    await bot.send_message(chat_id, txt)

@router.message()
async def any_msg(m: Message, bot: Bot):
    global wl

    if not is_private(m):
        return

    if m.from_user.id not in aut and is_admin_user(m.from_user):
        aut.add(m.from_user.id)

    if m.from_user.id not in aut:
        return

    mode = st_get(m.from_user.id, "mode")

    # ----- notify step 1 -----
    if mode == "nt_where":
        t = (m.text or "").split()
        if len(t) != 2:
            await m.answer("Нужно: chat_id thread_id\nПример: -100... 2")
            return
        try:
            cid = int(t[0])
            tid = int(t[1])
        except:
            await m.answer("Это должны быть числа.")
            return
        st_set(m.from_user.id, mode="nt_text", cid=cid, tid=tid)
        await m.answer("Теперь пришли текст (или txt документ).", reply_markup=kb_cancel())
        return

    # ----- notify step 2 -----
    if mode == "nt_text":
        cid = st_get(m.from_user.id, "cid")
        tid = st_get(m.from_user.id, "tid")

        txt = ""
        if m.text:
            txt = m.text.strip()
        if not txt and m.document:
            f = await bot.get_file(m.document.file_id)
            data = await bot.download_file(f.file_path)
            txt = data.read().decode("utf-8", errors="ignore").strip()
        if not txt:
            await m.answer("Пришли текст или TXT документом.")
            return


        wl = read_wl()
        out = txt + mentions_text()

        try:
            await send_to_target(bot, cid, tid, out)
        except Exception as e:
            await m.answer(f"Не смог отправить: {e}")
            return

        st_clear(m.from_user.id)
        await m.answer("Готово ✅", reply_markup=kb_main())
        return

    # ----- HW step: dt -----
    if mode == "hw_dt":
        try:
            dt = datetime.strptime((m.text or "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        except:
            await m.answer("Неверный формат. Пример: 2026-03-01 09:00")
            return
        st_set(m.from_user.id, mode="hw_subj", dt=dt.isoformat())
        await m.answer("Теперь напиши предмет.", reply_markup=kb_cancel())
        return

    # ----- HW step: subj -----
    if mode == "hw_subj":
        s = (m.text or "").strip()
        if not s:
            await m.answer("Предмет не пустой.")
            return
        st_set(m.from_user.id, mode="hw_body", subj=s)
        await m.answer("Теперь пришли ДЗ: текстом ИЛИ фото/файл/видео/аудио.", reply_markup=kb_cancel())
        return

    # ----- HW step: body -----
    if mode == "hw_body":
        dt_iso = st_get(m.from_user.id, "dt")
        subj = st_get(m.from_user.id, "subj")
        hw_mode = st_get(m.from_user.id, "hw_mode", 1)

        kind = ""
        txt = ""
        fid = ""

        if m.text:
            kind = "text"
            txt = m.text.strip()
        elif m.photo:
            kind = "photo"
            fid = m.photo[-1].file_id
            txt = (m.caption or "").strip()
        elif m.document:
            kind = "doc"
            fid = m.document.file_id
            txt = (m.caption or "").strip()
        elif m.video:
            kind = "video"
            fid = m.video.file_id
            txt = (m.caption or "").strip()
        elif m.audio:
            kind = "audio"
            fid = m.audio.file_id
            txt = (m.caption or "").strip()
        else:
            await m.answer("Пришли текст или медиа.")
            return


        wl = read_wl()

        if hw_mode == 1:
            dt = datetime.fromisoformat(dt_iso)
            try:
                await send_hw_item(bot, dt, subj, kind, txt, fid)
            except Exception as e:
                await m.answer(f"Не смог отправить в тему ДЗ: {e}")
                return

        await hw_add(dt_iso, subj, kind, txt, fid, hw_mode)
        st_clear(m.from_user.id)
        await m.answer("Ок ✅", reply_markup=kb_main())
        return

    # ----- HISTORY create: dt -----
    if mode == "hh_dt":
        try:
            dt = datetime.strptime((m.text or "").strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        except:
            await m.answer("Неверный формат. Пример: 2026-03-01 09:00")
            return
        hid = await hh_add(dt.isoformat())
        st_set(m.from_user.id, mode="hh_s", hid=hid)
        await m.answer("Сколько семинаров? (число)", reply_markup=kb_cancel())
        return

    # ----- HISTORY: sem count -----
    if mode == "hh_s":
        t = (m.text or "").strip()
        try:
            a = [int(x) for x in t.replace(",", " ").split()]
            a = sorted(set(a))
            if not a:
                raise ValueError()
            if any(x < 1 or x > 999 for x in a):
                raise ValueError()
        except:
            await m.answer("Напиши номера семинаров через пробел. Пример: 5 8 10")
            return

        st_set(m.from_user.id, mode="hh_sem_tn", sem_list=a, idx=0)
        await m.answer(f"Семинар {a[0]}: сколько тем?", reply_markup=kb_cancel())
        return

    # ----- HISTORY: topics count for sem -----
    if mode == "hh_sem_tn":
        try:
            k = int((m.text or "").strip())
            if k < 1 or k > 200:
                raise ValueError()
        except:
            await m.answer("Нужно число 1..200")
            return
        st_set(m.from_user.id, mode="hh_topic", k=k, j=1)
        sem_list = st_get(m.from_user.id, "sem_list", [])
        idx = st_get(m.from_user.id, "idx", 0)
        cur_sem = sem_list[idx]
        await m.answer(f"Семинар {cur_sem}: тема 1:", reply_markup=kb_cancel())
        return

    # ----- HISTORY: entering topics -----
    if mode == "hh_topic":
        hid = st_get(m.from_user.id, "hid")
        sem_list = st_get(m.from_user.id, "sem_list", [])
        idx = st_get(m.from_user.id, "idx", 0)
        cur_sem = sem_list[idx]
        k = st_get(m.from_user.id, "k")
        j = st_get(m.from_user.id, "j")

        t = (m.text or "").strip()
        if not t:
            await m.answer("Тема не пустая.")
            return
        await ht_add(hid, cur_sem, t)

        j += 1
        if j <= k:
            st_set(m.from_user.id, j=j)
            await m.answer(f"Семинар {cur_sem}: тема {j}:", reply_markup=kb_cancel())
            return

        sem_list = st_get(m.from_user.id, "sem_list", [])
        idx = st_get(m.from_user.id, "idx", 0)

        idx += 1
        if idx < len(sem_list):
            st_set(m.from_user.id, mode="hh_sem_tn", idx=idx)
            await m.answer(f"Семинар {sem_list[idx]}: сколько тем?", reply_markup=kb_cancel())
            return

        # всё, назначения
        st_set(m.from_user.id, mode="hh_assign", hid=hid)
        await m.answer("Темы введены ✅ Переходим к назначениям.")
        await show_assign_lists(bot, m.chat.id, hid)
        await m.answer(
            "Пиши: тема_номер человек_номер\n"
            "Пример: 3 1\n"
            "Снять: 3 0\n"
            "list — показать списки\n"
            "stop — выйти без отправки\n"
            "send — отправить/обновить в теме ДЗ",
            reply_markup=kb_cancel()
        )
        return

    # ----- HISTORY: assign -----
    if mode == "hh_assign":
        hid = st_get(m.from_user.id, "hid")
        txt = (m.text or "").strip().lower()

        if txt == "list":
            await show_assign_lists(bot, m.chat.id, hid)
            return

        if txt == "stop":
            st_clear(m.from_user.id)
            await m.answer("Ок. Вернул в меню.", reply_markup=kb_main())
            return

        if txt == "send":
            r = await hh_get(hid)
            if not r:
                await m.answer("Нет записи.")
                return

            _, dt_s, msg_id, *_ = r
            dt = datetime.fromisoformat(dt_s)
            ts = await ht_list(hid)
            out = hh_text(dt, ts)

            if msg_id:
                try:
                    await bot.delete_message(HW_CHAT_ID, msg_id)
                except:
                    pass

            new_id = await send_long(bot, HW_CHAT_ID, out, HW_THREAD_ID)
            await hh_set_msg(hid, new_id)

            st_clear(m.from_user.id)
            await m.answer("Отправил/обновил ✅", reply_markup=kb_main())
            return

        p = txt.split()
        if len(p) != 2:
            await m.answer("Нужно: тема_номер человек_номер (или list/stop/send)")
            return
        try:
            ti = int(p[0])
            ui = int(p[1])
        except:
            await m.answer("Это должны быть числа.")
            return

        wl = read_wl()
        ts = await ht_list(hid)

        if ti < 1 or ti > len(ts):
            await m.answer("Нет такой темы.")
            return

        tid = ts[ti - 1][0]

        if ui == 0:
            await ht_set_who(tid, "")
            await m.answer("Ок: снял назначение.")
            return

        if ui < 1 or ui > len(wl):
            await m.answer("Нет такого человека в белом списке.")
            return

        who = wl[ui - 1]
        await ht_set_who(tid, who)
        await m.answer(f"Ок: тема {ti} -> {who}")
        return

    # ----- HISTORY: rename -----
    if mode == "hh_rename":
        hid = st_get(m.from_user.id, "hid")
        t = (m.text or "").strip()
        p = t.split(maxsplit=1)
        if len(p) < 2:
            await m.answer("Пиши: тема_номер новый_текст")
            return
        try:
            ti = int(p[0])
        except:
            await m.answer("Первое должно быть число.")
            return
        newt = p[1].strip()
        ts = await ht_list(hid)
        if ti < 1 or ti > len(ts):
            await m.answer("Нет такой темы.")
            return
        tid = ts[ti - 1][0]
        await ht_set_title(tid, newt)
        st_clear(m.from_user.id)
        await m.answer("Переименовал ✅", reply_markup=kb_main())
        return

    # ----- HISTORY: random exclude + preview + buttons -----
    if mode == "hh_rand_ex":
        hid = st_get(m.from_user.id, "hid")
        wl = read_wl()

        s = (m.text or "").strip()
        ex = set()
        if s != "0":
            for x in s.split():
                try:
                    i = int(x)
                    if 1 <= i <= len(wl):
                        ex.add(wl[i - 1])
                except:
                    pass

        ts = await ht_list(hid)
        free = [x for x in ts if not x[3]]  # who empty
        used = [x[3] for x in ts if x[3]]
        used_set = set(used)

        cand = [u for u in wl if (u not in ex)]
        if not cand:
            await m.answer("Некого назначать (все исключены или список пуст).")
            st_clear(m.from_user.id)
            return

        cand1 = [u for u in cand if u not in used_set]
        cand2 = [u for u in cand]  # fallback
        base = cand1 if cand1 else cand2

        random.shuffle(base)

        pool = []
        while len(pool) < len(free):
            pool += base
        pool = pool[:len(free)]

        random.shuffle(free)
        for i in range(len(free)):
            await ht_set_who(free[i][0], pool[i])

        st_clear(m.from_user.id)

        r = await hh_get(hid)
        _, dt_s, msg_id, *_ = r
        dt = datetime.fromisoformat(dt_s)
        ts2 = await ht_list(hid)
        prev = "🎲 Рандом готов.\n\n" + hh_text(dt, ts2) + f"\n\nid: {hid}\nmsg_id: {msg_id}"
        await m.answer(prev)
        await m.answer("Теперь можешь изменить старое или отправить новое:", reply_markup=kb_hh_actions(hid))
        return


# ==================== TICK ====================

async def tick(bot: Bot):
    global wl
    wl = read_wl()
    now = datetime.now(TZ)

    # обычное дз
    rows = await hw_list_active()
    for i, dt_s, subj, kind, txt, fid, mode, r24, r10 in rows:
        dt = datetime.fromisoformat(dt_s)
        t24 = dt - timedelta(hours=24)
        t10 = dt - timedelta(hours=10)

        if r24 == 0 and abs((now - t24).total_seconds()) < 60:
            await send_hw_text(bot, f"⏰ Напоминание: до пары ~24 часа\n🗓 {fmt_dt(dt)}\n📚 {subj}" + mentions_text())
            await hw_set_flags(i, r24=1)

        if r10 == 0 and abs((now - t10).total_seconds()) < 60:
            await send_hw_text(bot, f"⏰ Напоминание: до пары ~10 часов\n🗓 {fmt_dt(dt)}\n📚 {subj}" + mentions_text())
            await hw_set_flags(i, r10=1)

        if now >= dt:
            await hw_set_flags(i, done=1)

    # история
    hh = await hh_list()
    for hid, dt_s, msg_id, r24, r10, done in hh:
        if done == 1:
            continue
        dt = datetime.fromisoformat(dt_s)
        t24 = dt - timedelta(hours=24)
        t10 = dt - timedelta(hours=10)

        if r24 == 0 and abs((now - t24).total_seconds()) < 60:
            await send_hw_text(bot, f"⏰ История: до пары ~24 часа\n🗓 {fmt_dt(dt)}" + mentions_text())
            await hh_set_flags(hid, r24=1)

        if r10 == 0 and abs((now - t10).total_seconds()) < 60:
            await send_hw_text(bot, f"⏰ История: до пары ~10 часов\n🗓 {fmt_dt(dt)}" + mentions_text())
            await hh_set_flags(hid, r10=1)

        if now >= dt:
            await hh_set_flags(hid, done=1)


# ==================== MAIN ====================

async def main():
    if not TOKEN:
        print("Нужен BOT_TOKEN")
        return
    if HW_CHAT_ID == 0 or HW_THREAD_ID == 0:
        print("Нужны HW_CHAT_ID и HW_THREAD_ID")
        return

    await db_init()

    global wl
    wl = read_wl()

    bot = Bot(TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    sch = AsyncIOScheduler(timezone=str(TZ))
    sch.add_job(tick, "interval", minutes=1, args=[bot])
    sch.start()

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())