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
```

สร้าง `server/.env` แล้วใส่ค่าแบบนี้:

```env
APP_CORS_ORIGINS=http://localhost:3000

MONGODB_URI=mongodb://127.0.0.1:27017
MONGODB_DATABASE=ocrstudio
MONGODB_JOBS_COLLECTION=ocr_jobs
MONGODB_IMPORTS_COLLECTION=ocr_imports

# OCR endpoint (Ollama native generate API)
OCR_BASE_URL=http://10.25.99.23:8200/api/generate
OCR_MODEL=scb10x/typhoon-ocr-7b:latest
OCR_API_KEY=

# Structured extraction (optional)
TEXT_BASE_URL=
TEXT_MODEL=
TEXT_API_KEY=
```

Note: MongoDB is now required for both OCR jobs and folder-review records. The old SQLite backend has been removed from the server code.

## Folder Review Flow

Current review flow uses one storage base under `server/data/storage`.

Add scanned files by either:
- Uploading from the web UI, or
- Dropping files into:

```text
server/data/storage/incoming
```

Before OCR, the backend automatically prepares cleaned inputs (watermark/noise reduction),
then stores assets in:

```text
server/data/storage/original/<import_id>/source.<ext>
server/data/storage/derived/<import_id>/
```

Review metadata is stored in Mongo collection:

```text
ocrstudio.ocr_imports
```

Each new scanned file is created as `review_ready` first. After a reviewer confirms the
document in the web UI, the same Mongo record is updated to `checked` with
`checked_at`, optional `checked_by`, and optional `note`.

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

