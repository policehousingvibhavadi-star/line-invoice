"""อ่านรายงานค่าธรรมเนียม/สาธารณูปโภคบ้านพัก ตร. (PDF จาก สก.) -> JSON
ใช้: python parse_report.py report.pdf > data.json   (ต้องมี poppler: pdftotext)"""
import re, sys, json, subprocess

NUM = r"([\d,]+\.\d\d)"
ROW = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(\d+/\d{3})\s+(\d{2})\s+(.+?)\s+(\d+)\s+(\d+)\s+(\d+)\s+"
    + r"\s+".join([NUM] * 4) + r"\s*(.*)$")
f = lambda s: float(s.replace(",", ""))

# ฟอนต์เก่าของรายงานใช้ตัวอักษรไทยในพื้นที่ PUA (วรรณยุกต์/สระลอย) -> แปลงเป็นยูนิโค้ดมาตรฐาน
PUA = {0xF701: 0x0E34, 0xF702: 0x0E35, 0xF703: 0x0E36, 0xF704: 0x0E37,
       0xF70A: 0x0E48, 0xF70B: 0x0E49, 0xF70C: 0x0E4A, 0xF70D: 0x0E4B, 0xF70E: 0x0E4C,
       0xF710: 0x0E31, 0xF711: 0x0E4D, 0xF712: 0x0E47, 0xF713: 0x0E48, 0xF714: 0x0E49,
       0xF715: 0x0E4A, 0xF716: 0x0E4B, 0xF717: 0x0E4C, 0xF718: 0x0E38, 0xF719: 0x0E39, 0xF71A: 0x0E3A}
def clean(t):
    t = t.translate(PUA).replace("\u0e4d\u0e32", "\u0e33")
    return re.sub("[\uf700-\uf8ff]", "", t)

def parse(pdf):
    txt = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, text=True).stdout
    txt = clean(txt)
    month = re.search(r"งวดประจำเดือน\s+(\S+\s+\d{4})", txt)
    rows, errors = [], []
    for line in txt.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        g = m.groups()
        parts = re.split(r"\s{2,}|\s(?=\S+\s+\S+$)", g[4].strip())
        w = g[4].split()
        rank, first, last = (w[0], w[1], " ".join(w[2:])) if len(w) >= 3 else ("", w[0], w[-1])
        r = dict(no=int(g[0]), site=g[1], room=g[2], building=g[3], rank=rank, first=first, last=last,
                 prev=int(g[5]), curr=int(g[6]), usage=int(g[7]), fee=f(g[8]), water=f(g[9]),
                 elec=f(g[10]), total=f(g[11]), remark=g[12].strip())
        exp_water = max(50, 17 * r["usage"])            # หน่วยละ 17 ขั้นต่ำ 50
        if r["curr"] - r["prev"] != r["usage"] or abs(exp_water - r["water"]) > 1 \
           or abs(r["fee"] + r["water"] + r["elec"] - r["total"]) > 0.01:
            errors.append(r["room"])
        rows.append(r)
    tot = re.search(r"รวม\s+(\d+)\s+ราย\s+จำนวนเงิน\s+" + r"\s+".join([NUM] * 4), txt)
    check = {}
    if tot:
        n, a, b, c, d = tot.groups()
        check = dict(count_ok=int(n) == len(rows), sum_ok=abs(sum(x["total"] for x in rows) - f(d)) < 0.01)
    return dict(month=month.group(1) if month else "", rows=rows, row_errors=errors, check=check)

if __name__ == "__main__":
    print(json.dumps(parse(sys.argv[1]), ensure_ascii=False, indent=1))
