# TR OCR Accuracy Setup

เอกสารนี้เป็นคู่มือปรับความแม่น OCR สำหรับเอกสาร TR โดยใช้ model ที่โปรเจคนี้รองรับอยู่แล้ว:

- Typhoon OCR เป็นตัวอ่านเอกสารหลัก
- Qwen VL เป็นตัวอ่าน/ตรวจ crop เฉพาะ field ที่เสี่ยง เช่น ชื่อ, ชื่อบิดา, ชื่อมารดา, ที่อยู่

เป้าหมายคือเพิ่มความแม่นจากประมาณ 80% ไปใกล้ 90% โดยเริ่มจาก tuning pipeline เดิมก่อนเพิ่มระบบใหม่อย่าง n8n หรือ cloud AI.

## สรุปคำแนะนำ

ให้ปรับตามลำดับนี้ก่อน:

1. เพิ่มขนาดภาพที่ส่งเข้า OCR
2. เพิ่มจำนวน output token ของ Typhoon
3. ใช้ Qwen VL เป็น field-crop verifier ต่อไป แต่ยังไม่ควรใช้ Qwen อ่านทั้งหน้าแทน Typhoon
4. เก็บชุดทดสอบจริง 50-100 หน้า แล้ววัด accuracy แยกตาม field
5. ถ้า field ชื่อยังผิดบ่อย ค่อยปรับ logic voting ระหว่าง Typhoon full-page, Typhoon crop, และ Qwen crop

## Current Model Roles

ค่าที่ใช้อยู่ตอนนี้ใน `server/.env`:

```env
OCR_BASE_URL=http://10.25.11.133:8200/api/generate
OCR_MODEL=scb10x/typhoon-ocr1.5-3b:latest
OCR_COMPARE_MODELS=
OCR_TARGET_IMAGE_DIM=900
OCR_TIMEOUT_SECONDS=120
OCR_NUM_PREDICT=400

VISION_ENABLED=true
VISION_BASE_URL=http://10.25.11.133:8200/api/generate
VISION_MODEL=qwen3-vl:32b-instruct
VISION_TIMEOUT_SECONDS=300
VISION_NUM_PREDICT=800

TR_CROP_OCR_MODE=selective
TR_VISION_FIELD_RESCUE_MODE=selective
TR_PARENT_NAME_REVIEW_REQUIRED=false
```

ความหมาย:

- `OCR_MODEL` คือ Typhoon OCR ใช้อ่านหน้าเอกสารและอ่าน crop ด้วย OCR
- `VISION_MODEL` คือ Qwen VL ใช้ตรวจ/อ่าน crop เฉพาะ field
- `OCR_COMPARE_MODELS` ว่าง หมายความว่ายังไม่มี OCR model ตัวที่สองสำหรับ full-page comparison
- `OCR_TARGET_IMAGE_DIM=900` คือค่า production fast path สำหรับให้ Typhoon อ่านทั้งหน้าเร็ว
- `OCR_NUM_PREDICT=400` ลดเวลารอ output เพราะเอกสาร TR มีโครงสร้างสั้น

## Recommended `.env`

เริ่มจาก config นี้ก่อน เพราะไม่เปลี่ยน architecture และยังใช้เครื่อง model เดิม:

```env
OCR_BASE_URL=http://10.25.11.133:8200/api/generate
OCR_MODEL=scb10x/typhoon-ocr1.5-3b:latest
OCR_COMPARE_MODELS=
OCR_TARGET_IMAGE_DIM=900
OCR_TIMEOUT_SECONDS=120
OCR_NUM_PREDICT=400
OCR_API_KEY=

VISION_ENABLED=true
VISION_BASE_URL=http://10.25.11.133:8200/api/generate
VISION_MODEL=qwen3-vl:32b-instruct
VISION_TIMEOUT_SECONDS=300
VISION_NUM_PREDICT=800
VISION_API_KEY=

TR_CROP_OCR_ENABLED=false
TR_CROP_OCR_MODE=selective
TR_VISION_FIELD_RESCUE_ENABLED=false
TR_VISION_FIELD_RESCUE_MODE=selective
TR_PARENT_NAME_REVIEW_REQUIRED=false
```

ถ้าต้องการเริ่มแบบ conservative กว่านี้ ให้ลดลงมาทดสอบก่อน:

Benchmark/accuracy mode is slower. Use this only for targeted testing, not the normal upload path:

```env
OCR_TARGET_IMAGE_DIM=1800
OCR_NUM_PREDICT=1200
OCR_TIMEOUT_SECONDS=180
TR_CROP_OCR_MODE=aggressive
TR_VISION_FIELD_RESCUE_MODE=aggressive
```

อย่าเพิ่งใส่ `qwen3-vl:32b-instruct` ใน `OCR_COMPARE_MODELS` เว้นแต่ตั้งใจ benchmark โดยเฉพาะ เพราะ Qwen เป็น vision-language model ทั่วไป เหมาะกับ crop verification มากกว่าอ่าน TR ทั้งหน้าเป็น OCR หลัก.

## Model Server Setup

ตัวอย่างนี้สมมติว่า model server ใช้ Ollama native `/api/generate`.

ติดตั้ง Ollama บนเครื่อง model:

```powershell
winget install Ollama.Ollama
```

หรือบน Linux:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

โหลด model:

```powershell
ollama pull scb10x/typhoon-ocr1.5-3b:latest
ollama pull qwen3-vl:32b-instruct
```

รัน Ollama ให้เครื่อง backend เรียกได้:

```powershell
$env:OLLAMA_HOST="0.0.0.0:8200"
ollama serve
```

ถ้าใช้ default port ของ Ollama ให้เปลี่ยน `.env` เป็น:

```env
OCR_BASE_URL=http://10.25.11.133:11434/api/generate
VISION_BASE_URL=http://10.25.11.133:11434/api/generate
```

ถ้า model server ของคุณ expose port `8200` อยู่แล้ว ให้ใช้ `http://10.25.11.133:8200/api/generate` ต่อได้เลย.

## Backend Setup

ติดตั้ง dependency:

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
```

หลังแก้ `server/.env` ให้ restart backend:

```powershell
cd server
.\.venv\Scripts\uvicorn app.main:app --reload --port 8000
```

## Test Model Endpoint

ทดสอบว่า endpoint ตอบได้:

```powershell
$body = @{
  model = "qwen3-vl:32b-instruct"
  prompt = "Return JSON only: {`"ok`":true}"
  stream = $false
  options = @{
    temperature = 0
    num_predict = 50
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri "http://10.25.11.133:8200/api/generate" -Body $body -ContentType "application/json"
```

ถ้าไม่ได้ response ให้เช็ค firewall, port, และ `OLLAMA_HOST`.

## Rerun OCR หลังปรับค่า

หลังแก้ `.env` และ restart backend แล้ว สามารถ rerun OCR batch เดิมได้.

Rerun ทั้ง batch:

```powershell
cd server
.\.venv\Scripts\python scripts\rerun_batch_ocr.py <batch_id>
```

Rerun เฉพาะบางหน้า:

```powershell
cd server
.\.venv\Scripts\python scripts\rerun_records_ocr.py <batch_id> --pages 2,3,6
```

ถ้าต้องการ rerun โดยไม่ reset record:

```powershell
cd server
.\.venv\Scripts\python scripts\rerun_batch_ocr.py <batch_id> --no-reset
```

## วิธีวัดผล

ให้ทำ spreadsheet หรือ JSON fixture สำหรับเอกสารจริงอย่างน้อย 50-100 หน้า โดยเก็บค่า expected field เหล่านี้:

- `personName`
- `motherName`
- `fatherName`
- `personId`
- `houseCode`
- `birthDate`
- `address`
- `updateDate`

ให้วัดแยก field อย่าวัดรวมทั้งหน้าอย่างเดียว เพราะชื่อไทยเป็น field ที่ผิดยากที่สุดและมีผลต่อ production มากที่สุด.

ตัวอย่าง metric:

```text
personName accuracy = จำนวนหน้าที่ชื่อถูก / จำนวนหน้าทั้งหมด
motherName accuracy = จำนวนหน้าที่ชื่อมารดาถูก / จำนวนหน้าที่มีข้อมูลมารดา
fatherName accuracy = จำนวนหน้าที่ชื่อบิดาถูก / จำนวนหน้าที่มีข้อมูลบิดา
critical-field accuracy = record ที่ personName + personId + houseCode ถูกทั้งหมด / record ทั้งหมด
```

Run field-level evaluation from saved batch metadata:

```powershell
cd server
.\.venv\Scripts\python scripts\evaluate_tr_accuracy.py <batch_metadata.json> <expected_fixture.json>
```

Fixture example:

```json
[
  {
    "record_id": "record-id-from-batch",
    "expected": {
      "personName": "นายตัวอย่าง ทดสอบ",
      "personId": "1-2345-67890-12-3",
      "houseCode": "1234-123456-1"
    }
  }
]
```

## Why Names Still Fail

เลขบัตร, รหัสบ้าน, วันที่ ตรวจด้วย pattern ได้ แต่ชื่อไทยไม่มี checksum. ถ้า OCR อ่านผิดจาก `อำ` เป็น `อา` หรือวรรณยุกต์หาย ค่าอาจยังดูเป็นชื่อไทยที่ valid อยู่ ระบบจึงจับผิดยาก.

จุดที่ต้องระวัง:

- ภาพเล็กเกินไปทำให้สระ/วรรณยุกต์หาย
- crop กว้างเกินไปทำให้ติดข้อความข้างเคียง
- crop แคบเกินไปทำให้ตัดตัวท้ายชื่อ
- cleaned image อาจช่วยลายน้ำ แต่บางครั้งทำลายเส้นบาง ๆ ของตัวอักษรไทย
- Qwen อาจอ่าน crop ถูกกว่า Typhoon ในบาง field แต่ก็มีโอกาสเดา จึงควรใช้เป็น verifier ไม่ใช่ overwrite ทุกครั้ง

## Next Code Improvement

ถ้าปรับ `.env` แล้วยังไม่ถึงเป้าหมาย ให้ทำ field-level voting:

1. Typhoon full-page อ่านชื่อ
2. Typhoon crop อ่านชื่อซ้ำ
3. Qwen crop อ่านชื่อซ้ำ
4. ถ้า 2 ใน 3 ตรงกัน ให้เลือกค่านั้น
5. ถ้าไม่ตรงกัน ให้ตั้ง `reviewStatus=needs_review` และเก็บ alternatives ให้ staff เห็น

ไฟล์ที่เกี่ยวข้อง:

- `server/app/manager_uploads.py` สำหรับ manager upload OCR, crop OCR, Qwen vision rescue, และ metadata
- `server/app/import_pipeline.py` สำหรับ legacy/import OCR path
- `server/app/tr_review.py` สำหรับ TR parser, field template, bbox, validation, และ name alternatives
- `server/app/core/config.py` สำหรับค่า default ของ OCR/Vision

## When To Add n8n

เพิ่ม n8n เมื่ออยากจัด workflow ภายนอก เช่น:

- แจ้งเตือน record ที่ `ocr_quality=needs_review`
- ส่ง low-confidence crop ไปให้ model cloud เฉพาะเคส
- ทำ retry schedule
- export result หลัง staff confirm

ไม่ควรใช้ n8n เป็นคำตอบแรกของ accuracy เพราะ n8n ไม่ได้อ่านชื่อแม่นขึ้นเอง ถ้าต้นทาง OCR/crop ยังผิด.
