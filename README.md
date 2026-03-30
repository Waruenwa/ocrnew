# Typhoon OCR Studio

MVP สำหรับเว็บ OCR เอกสารด้วย Typhoon โดยแยกเป็น:

- `server`: FastAPI สำหรับอัปโหลดไฟล์, คิวประมวลผล OCR, และเก็บผลลัพธ์
- `client`: Next.js สำหรับอัปโหลดเอกสาร, ติดตามสถานะ, และดูผล OCR

## ฟีเจอร์

- รองรับ `PDF`, `PNG`, `JPG`, `JPEG`
- ประมวลผล PDF ทีละหน้าเพื่อควบคุม progress
- มี job queue แบบง่ายเพื่อไม่ยิง API OCR พร้อมกันเกินจำเป็น
- เก็บผลลัพธ์ไว้ใน SQLite พร้อม list งานล่าสุด
- มี optional structured extraction ด้วย text model ของ Typhoon

## 1. รัน Server

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

แก้ `server/.env`:

```env
TYPHOON_OCR_API_KEY=your_typhoon_key
TYPHOON_API_KEY=your_typhoon_key
APP_CORS_ORIGINS=http://localhost:3000
```

จากนั้นรัน:

```powershell
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

## 2. รัน Client

```powershell
cd client
npm install
Copy-Item .env.example .env.local
```

ถ้าต้องการเปลี่ยน API URL:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

จากนั้นรัน:

```powershell
npm run dev
```

เปิด `http://localhost:3000`

## 3. Endpoint หลัก

- `GET /api/health`
- `GET /api/config`
- `GET /api/jobs`
- `GET /api/jobs/{job_id}`
- `POST /api/jobs`

## หมายเหตุ

- OCR ใช้แพ็กเกจทางการ `typhoon-ocr`
- Text extraction ใช้ OpenAI-compatible client ที่ `https://api.opentyphoon.ai/v1`
- ข้อมูลและไฟล์อัปโหลดจะถูกเก็บไว้ใน `server/data/`

