"""แจ้งหนี้/ใบเสร็จบ้านพัก ผ่าน LINE OA (หนึ่งโปรแกรม รองรับหลาย OA ตามที่พัก)"""
import os, re, json, hmac, hashlib, base64, sqlite3, tempfile, datetime, secrets
from html import escape as esc
from urllib.parse import quote
from zoneinfo import ZoneInfo
import httpx
from fastapi import FastAPI, Request, UploadFile, File, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, PlainTextResponse, FileResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from parse_report import parse

# ที่พักแต่ละแห่ง = LINE OA หนึ่งบัญชี (ใส่ secret/token ในตัวแปรสภาพแวดล้อม ไม่ใส่ในโค้ด)
SITES = {
    "ladyao":    {"name": "ลาดยาว",  "secret": os.getenv("LINE_LADYAO_SECRET", ""),    "token": os.getenv("LINE_LADYAO_TOKEN", "")},
    "vibhavadi": {"name": "วิภาวดี", "secret": os.getenv("LINE_VIBHAVADI_SECRET", ""), "token": os.getenv("LINE_VIBHAVADI_TOKEN", "")},
}
SITE_BY_NAME = {v["name"]: k for k, v in SITES.items()}
ACC_NAME = os.getenv("BANK_ACCOUNT_NAME", "กองทุนสวัสดิการอาคารบ้านพักส่วนกลาง")
ACC_NO = os.getenv("BANK_ACCOUNT_NO", "")
BANK = os.getenv("BANK_NAME", "ธนาคารกรุงไทย")
MONTHS = ["มกราคม","กุมภาพันธ์","มีนาคม","เมษายน","พฤษภาคม","มิถุนายน","กรกฎาคม","สิงหาคม","กันยายน","ตุลาคม","พฤศจิกายน","ธันวาคม"]
DB = os.path.join(os.getenv("DATA_DIR", "."), "app.db")
SLIP_DIR = os.path.join(os.getenv("DATA_DIR", "."), "slips")
app = FastAPI()
basic = HTTPBasic()

def db():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.executescript("""create table if not exists invoices(id integer primary key, site text, month text, month_key int,
      room text, name text, usage int, fee real, water real, elec real, total real, remark text,
      status text default 'unpaid', sent_at text, paid_at text, unique(site, month_key, room));
      create table if not exists bindings(id integer primary key, site text, user_id text, room text,
      status text default 'pending', unique(site, user_id));
      create table if not exists slips(id integer primary key, site text, room text, user_id text, file text, sha text,
      dup int default 0, invoice_id int, status text default 'pending', created_at text);""")
    return c

def auth(cr: HTTPBasicCredentials = Depends(basic)):
    pw = os.getenv("ADMIN_PASSWORD", "")
    if not pw: raise HTTPException(503, "ยังไม่ได้ตั้ง ADMIN_PASSWORD")
    if not secrets.compare_digest(cr.password, pw):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

def month_key(m):  # "ตุลาคม 2569" -> 256910
    n, y = m.split(); return int(y) * 100 + MONTHS.index(n) + 1

def today_th():
    d = datetime.datetime.now(ZoneInfo("Asia/Bangkok")); return f"{d.day} {MONTHS[d.month-1]} {d.year+543}"

N = lambda x: f"{x:,.2f}"
def row_(a, b): return {"type": "box", "layout": "horizontal", "contents": [
    {"type": "text", "text": a, "size": "sm", "color": "#6b7385"}, {"type": "text", "text": b, "size": "sm", "align": "end"}]}

def flex_invoice(i):
    return {"type": "flex", "altText": f"ใบแจ้งหนี้ห้อง {i['room']} งวด{i['month']}", "contents": {"type": "bubble",
      "header": {"type": "box", "layout": "vertical", "backgroundColor": "#06c755", "contents": [
          {"type": "text", "text": f"ใบแจ้งหนี้ งวด{i['month']}", "color": "#ffffff", "weight": "bold"},
          {"type": "text", "text": f"ห้อง {i['room']}", "color": "#ffffff", "size": "sm"}]},
      "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
          row_("ค่าธรรมเนียม", N(i["fee"])), row_(f"ค่าน้ำประปา ({i['usage']} หน่วย)", N(i["water"])),
          row_("ค่าไฟฟ้าส่วนกลาง", N(i["elec"])), {"type": "separator"},
          {"type": "text", "text": f"ยอดรวม {N(i['total'])} บาท", "weight": "bold", "size": "xl", "color": "#06a047"},
          {"type": "text", "text": "ชำระภายในวันที่ 5 ของเดือนถัดไป", "size": "xs", "color": "#6b7385"},
          {"type": "text", "text": f"โอนเข้าบัญชี {ACC_NAME}", "size": "sm", "wrap": True},
          {"type": "text", "text": f"{BANK} {ACC_NO}", "size": "sm", "weight": "bold"}]},
      "footer": {"type": "box", "layout": "vertical", "contents": [{"type": "button", "style": "secondary",
          "action": {"type": "clipboard", "label": "คัดลอกเลขบัญชี", "clipboardText": ACC_NO}}]}}}

def flex_receipt(i):
    return {"type": "flex", "altText": f"ใบเสร็จรับเงินห้อง {i['room']}", "contents": {"type": "bubble",
      "header": {"type": "box", "layout": "vertical", "backgroundColor": "#06a047", "contents": [
          {"type": "text", "text": "ใบเสร็จรับเงิน", "color": "#ffffff", "weight": "bold"},
          {"type": "text", "text": f"ห้อง {i['room']} · งวด{i['month']}", "color": "#ffffff", "size": "sm"}]},
      "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
          row_("เลขที่", f"RC-{i['month_key']}-{i['room'].replace('/', '')}"), row_("วันที่ชำระ", today_th()),
          row_("ค่าธรรมเนียม", N(i["fee"])), row_("ค่าน้ำประปา", N(i["water"])), row_("ค่าไฟฟ้าส่วนกลาง", N(i["elec"])),
          {"type": "separator"}, {"type": "text", "text": f"ชำระแล้ว {N(i['total'])} บาท", "weight": "bold", "size": "lg", "color": "#06a047"}]}}}

async def line(site, path, body):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post("https://api.line.me/v2/bot/message/" + path, json=body,
                         headers={"Authorization": "Bearer " + SITES[site]["token"]})
        r.raise_for_status()
async def fetch_content(site, mid):
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"https://api-data.line.me/v2/bot/message/{mid}/content", headers={"Authorization": "Bearer " + SITES[site]["token"]})
        r.raise_for_status(); return r.content
async def push(site, uid, msgs): await line(site, "push", {"to": uid, "messages": msgs})
async def reply(site, tok, msgs): await line(site, "reply", {"replyToken": tok, "messages": msgs})
T = lambda s: {"type": "text", "text": s}

# ---------- webhook (ผู้พักอาศัยพิมพ์เลขห้องเพื่อผูกไลน์ / พิมพ์ "ยอด" เพื่อดูใบแจ้งหนี้ล่าสุด) ----------
@app.post("/webhook/{site}")
async def webhook(site: str, req: Request):
    if site not in SITES or not SITES[site]["secret"]: raise HTTPException(404)
    body = await req.body()
    sig = base64.b64encode(hmac.new(SITES[site]["secret"].encode(), body, hashlib.sha256).digest()).decode()
    if not hmac.compare_digest(sig, req.headers.get("x-line-signature", "")): raise HTTPException(403)
    con = db()
    for ev in json.loads(body).get("events", []):
        uid, tok = ev.get("source", {}).get("userId"), ev.get("replyToken")
        if not uid or not tok: continue
        if ev["type"] == "follow":
            await reply(site, tok, [T("สวัสดีครับ พิมพ์เลขที่ห้องของท่าน เช่น 1/002 เพื่อลงทะเบียนรับใบแจ้งหนี้")]); continue
        if ev["type"] == "message" and ev["message"].get("type") == "image":      # สลิปโอนเงิน
            b = con.execute("select * from bindings where site=? and user_id=? and status='ok'", (site, uid)).fetchone()
            if not b:
                await reply(site, tok, [T("ยังไม่ได้ลงทะเบียนเลขห้อง กรุณาพิมพ์เลขห้อง (เช่น 1/002) ก่อน แล้วส่งสลิปอีกครั้ง")]); continue
            try: data = await fetch_content(site, ev["message"]["id"])
            except httpx.HTTPError:
                await reply(site, tok, [T("รับรูปไม่สำเร็จ กรุณาส่งสลิปอีกครั้ง")]); continue
            sha = hashlib.sha256(data).hexdigest()
            dup = con.execute("select 1 from slips where sha=?", (sha,)).fetchone() is not None
            os.makedirs(SLIP_DIR, exist_ok=True)
            fn = f"{site}_{ev['message']['id']}.jpg"
            with open(os.path.join(SLIP_DIR, fn), "wb") as f: f.write(data)
            inv = con.execute("select id from invoices where site=? and room=? and status='unpaid' order by month_key desc limit 1", (site, b["room"])).fetchone()
            con.execute("insert into slips(site,room,user_id,file,sha,dup,invoice_id,created_at) values(?,?,?,?,?,?,?,?)",
                        (site, b["room"], uid, fn, sha, int(dup), inv["id"] if inv else None, today_th())); con.commit()
            await reply(site, tok, [T("ได้รับสลิปแล้ว รอเจ้าหน้าที่ตรวจสอบและส่งใบเสร็จให้ครับ")]); continue
        if ev["type"] != "message" or ev["message"].get("type") != "text": continue
        txt = ev["message"]["text"].strip()
        m = re.search(r"(\d{1,2})\s*/\s*(\d{1,3})", txt)
        b = con.execute("select * from bindings where site=? and user_id=?", (site, uid)).fetchone()
        if m:
            room = f"{int(m[1])}/{int(m[2]):03d}"
            if not con.execute("select 1 from invoices where site=? and room=?", (site, room)).fetchone():
                await reply(site, tok, [T("ไม่พบเลขห้องนี้ในระบบ กรุณาตรวจสอบหรือติดต่อเจ้าหน้าที่")])
            else:
                con.execute("insert or replace into bindings(site,user_id,room,status) values(?,?,?,'pending')", (site, uid, room)); con.commit()
                await reply(site, tok, [T(f"รับคำขอลงทะเบียนห้อง {room} แล้ว รอเจ้าหน้าที่ยืนยันครับ")])
        elif "ยอด" in txt or "แจ้งหนี้" in txt:
            inv = b and b["status"] == "ok" and con.execute(
                "select * from invoices where site=? and room=? order by month_key desc limit 1", (site, b["room"])).fetchone()
            await reply(site, tok, [flex_invoice(inv)] if inv else [T("ยังไม่ได้ลงทะเบียน หรือรอเจ้าหน้าที่ยืนยัน")])
        else:
            await reply(site, tok, [T("พิมพ์เลขห้อง (เช่น 1/002) เพื่อลงทะเบียน หรือพิมพ์ \"ยอด\" เพื่อดูใบแจ้งหนี้ล่าสุด")])
    return PlainTextResponse("ok")

# ---------- หน้าเจ้าหน้าที่ ----------
def page(body):
    return HTMLResponse("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<style>body{font:15px system-ui;max-width:960px;margin:16px auto;padding:0 12px}table{border-collapse:collapse;width:100%}"
        "td,th{border-bottom:1px solid #ddd;padding:5px;text-align:left}button{padding:4px 10px}.w{color:#b45309}</style>" + body)
back = lambda m: RedirectResponse("/admin?msg=" + quote(m), 303)

@app.get("/admin")
def admin(msg: str = "", _=Depends(auth)):
    con, o = db(), [f"<h2>แจ้งหนี้ผ่านไลน์</h2><p><b>{esc(msg)}</b></p>",
        "<form method=post action=/admin/upload enctype=multipart/form-data><input type=file name=pdf accept=.pdf required> <button>อัปโหลดรายงาน PDF</button></form>"]
    pend = con.execute("select * from bindings where status='pending'").fetchall()
    if pend:
        o.append("<h3>คำขอลงทะเบียนรอยืนยัน</h3><p class=w>ตรวจว่าเป็นผู้พักอาศัยห้องนั้นจริงก่อนกดยืนยัน</p><table>")
        for p in pend:
            o.append(f"<tr><td>{esc(SITES[p['site']]['name'])}</td><td>ห้อง {esc(p['room'])}</td><td>"
                     f"<form method=post action=/admin/bind/{p['id']}><button name=ok value=1>ยืนยัน</button> <button name=ok value=0>ปฏิเสธ</button></form></td></tr>")
        o.append("</table>")
    sl = con.execute("select s.*, i.total, i.month, i.name from slips s left join invoices i on i.id=s.invoice_id where s.status='pending' order by s.id").fetchall()
    if sl:
        o.append("<h3>สลิปรอตรวจ</h3><p class=w>เทียบยอดกับเงินที่เข้าบัญชีจริงก่อนกดยืนยัน โปรแกรมยังไม่ได้อ่านยอดจากรูปสลิป</p>")
        for p in sl:
            info = (f"ยอดที่ต้องชำระ <b>{N(p['total'])}</b> บาท งวด{esc(p['month'])}" if p["invoice_id"] else "<span class=w>ไม่พบยอดค้างชำระของห้องนี้</span>")
            o.append(f"<div style='border:1px solid #ddd;padding:8px;margin:6px 0;display:flex;gap:12px;flex-wrap:wrap'>"
                     f"<a href=/admin/slip/{p['id']}/img target=_blank><img src=/admin/slip/{p['id']}/img style='width:130px'></a><div>"
                     f"{esc(SITES[p['site']]['name'])} ห้อง {esc(p['room'])} {esc(p['name'] or '')}<br>{info}<br>ส่งเมื่อ {esc(p['created_at'])}"
                     + ("<br><b class=w>รูปสลิปนี้เคยถูกส่งมาแล้ว ตรวจให้ดี</b>" if p["dup"] else "") + "<br>"
                     + (f"<form method=post action=/admin/slip/{p['id']}/ok style=display:inline><button>ยืนยันรับชำระ+ส่งใบเสร็จ</button></form> " if p["invoice_id"] else "")
                     + f"<form method=post action=/admin/slip/{p['id']}/no style=display:inline><button>ปฏิเสธ</button></form></div></div>")
    okb = con.execute("select * from bindings where status='ok' order by site, room").fetchall()
    if okb:
        o.append("<h3>ผู้ที่ผูกไลน์แล้ว</h3><table>")
        for p in okb:
            o.append(f"<tr><td>{esc(SITES[p['site']]['name'])}</td><td>ห้อง {esc(p['room'])}</td><td>"
                     f"<form method=post action=/admin/unbind/{p['id']} onsubmit=\"return confirm('ยกเลิกการผูกไลน์ของห้องนี้?')\"><button>ยกเลิกการผูก</button></form></td></tr>")
        o.append("</table>")
    for key, s in SITES.items():
        mk = con.execute("select max(month_key) k from invoices where site=?", (key,)).fetchone()["k"]
        if not mk: continue
        rows = con.execute("select i.*, (select count(*) from bindings b where b.site=i.site and b.room=i.room and b.status='ok') bound "
                           "from invoices i where site=? and month_key=? order by room", (key, mk)).fetchall()
        o.append(f"<h3>{esc(s['name'])} งวด{esc(rows[0]['month'])} ({len(rows)} ราย)</h3>"
                 f"<form method=post action=/admin/send/{key}/{mk} onsubmit=\"return confirm('ส่งใบแจ้งหนี้ให้ผู้ที่ลงทะเบียนแล้วทุกห้อง?')\"><button>ส่งใบแจ้งหนี้</button></form>"
                 "<table><tr><th>ห้อง</th><th>ชื่อ</th><th>ยอด</th><th>หมายเหตุ</th><th>ไลน์</th><th>สถานะ</th><th></th></tr>")
        for r in rows:
            paid = r["status"] == "paid"
            o.append(f"<tr><td>{esc(r['room'])}</td><td>{esc(r['name'])}</td><td>{N(r['total'])}</td><td>{esc(r['remark'] or '')}</td>"
                     f"<td>{'ผูกแล้ว' if r['bound'] else '-'}</td><td>{'ชำระแล้ว' if paid else ('ส่งแล้ว' if r['sent_at'] else 'ยังไม่ส่ง')}</td><td>"
                     + (f"<form method=post action=/admin/unpay/{r['id']} onsubmit=\"return confirm('ย้อนสถานะเป็นยังไม่ชำระ?')\"><button>ย้อนสถานะ</button></form>" if paid
                        else f"<form method=post action=/admin/send1/{r['id']} style=display:inline><button>{'ส่งซ้ำ' if r['sent_at'] else 'ส่งใบแจ้งหนี้'}</button></form> "
                           f"<form method=post action=/admin/paid/{r['id']} style=display:inline><button>รับชำระ+ส่งใบเสร็จ</button></form>") + "</td></tr>")
        o.append("</table>")
    return page("".join(o))

@app.post("/admin/upload")
async def upload(pdf: UploadFile = File(...), _=Depends(auth)):
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(await pdf.read()); f.flush(); d = parse(f.name)
    if not d["rows"] or not d["month"]: return back("อ่านรายงานไม่ได้ ไม่ได้นำเข้าข้อมูล")
    if d["row_errors"] or not (d["check"].get("sum_ok") and d["check"].get("count_ok")):
        return back(f"ตัวเลขในรายงานไม่ตรงกัน (ห้อง: {', '.join(d['row_errors']) or 'ผลรวมท้ายรายงาน'}) ไม่ได้นำเข้าข้อมูล")
    unknown = {r["site"] for r in d["rows"]} - set(SITE_BY_NAME)
    if unknown: return back("ไม่รู้จักที่พัก: " + ", ".join(unknown))
    con, mk = db(), month_key(d["month"])
    for r in d["rows"]:
        con.execute("insert into invoices(site,month,month_key,room,name,usage,fee,water,elec,total,remark) values(?,?,?,?,?,?,?,?,?,?,?) "
            "on conflict(site,month_key,room) do update set usage=excluded.usage,fee=excluded.fee,water=excluded.water,elec=excluded.elec,"
            "total=excluded.total,remark=excluded.remark,name=excluded.name",
            (SITE_BY_NAME[r["site"]], d["month"], mk, r["room"], f"{r['rank']}{r['first']} {r['last']}", r["usage"], r["fee"], r["water"], r["elec"], r["total"], r["remark"]))
    con.commit()
    return back(f"นำเข้างวด{d['month']} {len(d['rows'])} ราย ตรวจยอดตรงกับท้ายรายงานแล้ว")

@app.post("/admin/bind/{bid}")
def bind(bid: int, ok: int = Form(...), _=Depends(auth)):
    con = db()
    con.execute("update bindings set status='ok' where id=?", (bid,)) if ok else con.execute("delete from bindings where id=?", (bid,))
    con.commit(); return back("บันทึกแล้ว")

@app.post("/admin/unpay/{iid}")
def unpay(iid: int, _=Depends(auth)):
    con = db(); con.execute("update invoices set status='unpaid', paid_at=null where id=?", (iid,)); con.commit()
    return back("ย้อนสถานะเป็นยังไม่ชำระแล้ว (ไม่มีข้อความส่งออก)")

@app.post("/admin/unbind/{bid}")
def unbind(bid: int, _=Depends(auth)):
    con = db(); b = con.execute("select * from bindings where id=?", (bid,)).fetchone()
    if b: con.execute("update invoices set sent_at=null where site=? and room=? and status='unpaid'", (b["site"], b["room"]))
    con.execute("delete from bindings where id=?", (bid,)); con.commit()
    return back("ยกเลิกการผูกแล้ว (ห้องนี้จะถูกนับเป็นยังไม่ส่ง)")

async def send_to_room(con, inv, msg):
    n = bad = 0
    for b in con.execute("select user_id from bindings where site=? and room=? and status='ok'", (inv["site"], inv["room"])).fetchall():
        try: await push(inv["site"], b["user_id"], [msg]); n += 1
        except httpx.HTTPError: bad += 1
    return n, bad

@app.post("/admin/send/{site}/{mk}")
async def send(site: str, mk: int, _=Depends(auth)):
    con, ok, bad = db(), 0, 0
    for inv in con.execute("select * from invoices where site=? and month_key=? and status='unpaid' and sent_at is null", (site, mk)).fetchall():
        n, b = await send_to_room(con, inv, flex_invoice(inv)); ok += n; bad += b
        if n: con.execute("update invoices set sent_at=? where id=?", (today_th(), inv["id"]))
    con.commit(); return back(f"ส่งใบแจ้งหนี้ {ok} ข้อความ" + (f" ส่งไม่สำเร็จ {bad}" if bad else ""))

@app.post("/admin/send1/{iid}")
async def send1(iid: int, _=Depends(auth)):
    con = db(); inv = con.execute("select * from invoices where id=?", (iid,)).fetchone()
    n, bad = await send_to_room(con, inv, flex_invoice(inv))
    if n: con.execute("update invoices set sent_at=? where id=?", (today_th(), iid)); con.commit()
    if not n and not bad: return back(f"ห้อง {inv['room']} ยังไม่มีผู้ผูกไลน์ ไม่ได้ส่ง")
    return back(f"ส่งใบแจ้งหนี้ห้อง {inv['room']} {n} ข้อความ" + (f" ส่งไม่สำเร็จ {bad}" if bad else ""))

async def do_paid(con, iid):
    inv = con.execute("select * from invoices where id=?", (iid,)).fetchone()
    if inv["status"] == "paid": return f"ห้อง {inv['room']} ชำระแล้ว ไม่ได้ส่งใบเสร็จซ้ำ"
    con.execute("update invoices set status='paid', paid_at=? where id=?", (today_th(), iid)); con.commit()
    n, bad = await send_to_room(con, inv, flex_receipt(inv))
    return f"บันทึกรับชำระห้อง {inv['room']} ส่งใบเสร็จ {n} ข้อความ" + (f" ส่งไม่สำเร็จ {bad}" if bad else "")

@app.post("/admin/paid/{iid}")
async def paid(iid: int, _=Depends(auth)):
    return back(await do_paid(db(), iid))

@app.get("/admin/slip/{sid}/img")
def slip_img(sid: int, _=Depends(auth)):
    s = db().execute("select file from slips where id=?", (sid,)).fetchone()
    if not s: raise HTTPException(404)
    return FileResponse(os.path.join(SLIP_DIR, s["file"]), media_type="image/jpeg")

@app.post("/admin/slip/{sid}/ok")
async def slip_ok(sid: int, _=Depends(auth)):
    con = db(); s = con.execute("select * from slips where id=?", (sid,)).fetchone()
    if not s or not s["invoice_id"]: return back("ไม่พบยอดค้างชำระของสลิปนี้")
    msg = await do_paid(con, s["invoice_id"])
    con.execute("update slips set status='approved' where id=?", (sid,)); con.commit(); return back(msg)

@app.post("/admin/slip/{sid}/no")
async def slip_no(sid: int, _=Depends(auth)):
    con = db(); s = con.execute("select * from slips where id=?", (sid,)).fetchone()
    if not s: return back("ไม่พบสลิป")
    con.execute("update slips set status='rejected' where id=?", (sid,)); con.commit()
    try: await push(s["site"], s["user_id"], [T("เจ้าหน้าที่ตรวจสลิปแล้วยังไม่ถูกต้อง กรุณาตรวจสอบและส่งสลิปใหม่ หรือติดต่อเจ้าหน้าที่ครับ")]); note = "แจ้งผู้ส่งแล้ว"
    except httpx.HTTPError: note = "แจ้งผู้ส่งไม่สำเร็จ"
    return back("ปฏิเสธสลิปแล้ว " + note)

@app.get("/")
def root(): return PlainTextResponse("ok")
