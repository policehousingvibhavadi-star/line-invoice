# แจ้งหนี้/ใบเสร็จบ้านพักผ่านไลน์ OA

## ตัวแปรสภาพแวดล้อม (ตั้งใน Railway > Variables ห้ามใส่ในโค้ด)
| ชื่อ | ค่า |
|---|---|
| ADMIN_PASSWORD | รหัสผ่านเข้าหน้า /admin (ตั้งให้ยาวและเดายาก) |
| LINE_LADYAO_SECRET / LINE_LADYAO_TOKEN | Channel secret / Channel access token ของ OA ลาดยาว |
| LINE_VIBHAVADI_SECRET / LINE_VIBHAVADI_TOKEN | ของ OA วิภาวดี |
| BANK_ACCOUNT_NO | เลขบัญชีกองทุน |
| BANK_ACCOUNT_NAME, BANK_NAME | (ไม่ต้องตั้งก็ได้) ค่าเริ่มต้นคือ กองทุนสวัสดิการอาคารบ้านพักส่วนกลาง / ธนาคารกรุงไทย |
| DATA_DIR | /data (ต้องเพิ่ม Volume ที่ mount ไว้ที่ /data ไม่เช่นนั้นข้อมูลหายเมื่อรีสตาร์ท) |

## ติดตั้งบน Railway
1. สร้างโปรเจกต์ใหม่ อัปโหลดโฟลเดอร์นี้ (ผ่าน GitHub หรือ Railway CLI) Railway จะใช้ Dockerfile เอง
2. เพิ่ม Volume mount path `/data` และตั้งตัวแปรตามตารางด้านบน
3. Settings > Networking > Generate Domain จะได้ที่อยู่ เช่น https://xxxx.up.railway.app
4. ใน LINE Developers Console ของแต่ละ OA ช่อง Messaging API > Webhook URL ใส่
   - ลาดยาว: `https://xxxx.up.railway.app/webhook/ladyao`
   - วิภาวดี: `https://xxxx.up.railway.app/webhook/vibhavadi`
   แล้วกด Verify และเปิด Use webhook

## ใช้งานทุกเดือน
1. เปิด `/admin` อัปโหลด PDF จาก สก. (ระบบตรวจยอดกับท้ายรายงานก่อนนำเข้า ถ้าไม่ตรงจะไม่นำเข้า)
2. ผู้พักอาศัยเพิ่มเพื่อน OA แล้วพิมพ์เลขห้อง เช่น 1/002 คุณกดยืนยันในหน้า /admin (ทำครั้งเดียวต่อคน)
3. ตรวจรายการ (โดยเฉพาะรายที่หมายเหตุเป็นเกษียณ/ลาออก) แล้วกด "ส่งใบแจ้งหนี้"
4. เมื่อได้รับเงิน กด "รับชำระ+ส่งใบเสร็จ" ของห้องนั้น
