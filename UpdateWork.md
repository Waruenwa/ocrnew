# Update Work

## 2026-04-30: แยก pipeline version ของหมวด ทร ออกจาก flow คำพิพากษา

- แยก version เป็น 2 ค่าใน `server/app/import_pipeline.py`
  - `IMPORT_OCR_PIPELINE_VERSION = 71` สำหรับ flow เดิม/คำพิพากษา/หมวดทั่วไป
  - `TR_IMPORT_OCR_PIPELINE_VERSION = 77` สำหรับหมวด `tr` เท่านั้น
- เพิ่ม helper `_pipeline_version_for_category()` และ `_document_target_pipeline_version()` เพื่อให้การเช็ก cache/reprocess อิงตามหมวดเอกสาร ไม่ใช้ global version เดียวร่วมกัน
- ล็อกไม่ให้เอกสารคำพิพากษาเดิมถูกมองว่าล้าหลังเพียงเพราะงาน `tr` bump version เป็น `77`
- หมวด `tr` ยังใช้ flow แยก:
  - ลายน้ำใช้ `server/app/tr_watermark_cleaner.py`
  - review/parser ใช้ `server/app/tr_review.py`
  - OCR ใช้ cleaned image ของหมวด `tr` โดยตรง ไม่ไปยิง original/aggressive candidate ของ flow คำพิพากษา
- เพิ่ม guard ใน `process_import_ocr()` ถ้า OCR สำเร็จแต่ post-process/review parser error ระบบจะตั้ง `ocr_failed` พร้อม error message แทนการค้างที่ `ocr_running`
- ปรับ `server/app/tr_review.py` ให้ parse จาก OCR markdown รายบรรทัดก่อน fallback:
  - `เลขประจำตัวประชาชน`, `เลขรหัสประจำบ้าน`
  - `ชื่อ`, `เพศ`, `สัญชาติ`, `เกิดเมื่อ`, `อายุ`, `สถานภาพ`
  - `มารดาชื่อ`, `บิดาชื่อ`, nationality ของพ่อ/แม่
  - `ที่อยู่`, `เข้ามาอยู่เมื่อวันที่`, `บันทึกเพิ่มเติม`, `ปรับปรุงครั้งสุดท้าย`
- แก้ date parser ของ `tr` ให้รองรับรูปแบบ `วันที่ 2 เดือน พฤษภาคม พ.ศ. 2554` และ normalize เป็น `2 พฤษภาคม 2554`
- อัปเดต review data ของ import `f7f52daac62a4d8a8892865ca3e9e121` จาก OCR cache เดิมแล้ว ไม่ได้ยิง OCR ใหม่
- ผลล่าสุดของไฟล์ตัวอย่าง `แบบ 1.pdf`:
  - ชื่อ: `น.ส.ณัฐกาญจน์ ลีละดานนท์`
  - เพศ/สัญชาติ: `หญิง` / `ไทย`
  - วันเกิด: `24 มีนาคม 2528`
  - สถานภาพ: `เจ้าบ้าน`
  - มารดา: `ไขแข`, ID `3-8097-00176-90-5`
  - บิดา: `ไพโรจน์`, ID `3-8099-00060-41-2`
  - ที่อยู่: `190/2 ถนนรัชดาภิเษก แขวงห้วยขวาง เขตห้วยขวาง กรุงเทพมหานคร`
  - เข้ามาอยู่วันที่: `2 พฤษภาคม 2554`
  - Remark: `บุคคลนี้มีภูมิลำเนาอยู่ในบ้านนี้`
  - Update Date: `18 มีนาคม 2569`

## สถานะล่าสุด

- OCR หลักใช้ `scb10x/typhoon-ocr1.5-3b:latest`
- OCR endpoint ใช้ `http://10.25.99.23:8200/api/generate`
- Pipeline OCR version ล่าสุดเป็น `71`
- หน้า upload รองรับเลือกได้สูงสุด `10` ไฟล์ และจำกัดขนาด `50 MB ต่อไฟล์`; frontend ส่งเข้า endpoint เดิมทีละไฟล์เพื่อให้ OCR queue ทำงานเรียงทีละงาน
- Flow หลักยังเป็น `upload -> clean watermark -> OCR cleaned image -> review`
- Qwen/Vision ถูกปิดออกจาก baseline ตอนนี้แล้ว เพราะถ้า endpoint vision ไม่พร้อมจะทำให้หลัง OCR เสร็จแล้วยังค้างนาน
- Runtime ตอนนี้เป็น OCR-only: `Settings.vision_ready` คืนค่า `False` เสมอ จึงไม่มี flow ไหนเรียก Qwen
- ระบบมี health check สำหรับ OCR/Vision endpoint เพื่อ fail fast ไม่รอ timeout ยาวโดยไม่จำเป็น
- เพิ่ม anchor provider สำหรับ line bbox แล้ว: backend จะพยายามหา bbox ของบรรทัดหัวเอกสารจากภาพจริงก่อน เพื่อให้กรอบฝั่งซ้ายตรงกับ field ฝั่งขวา
- ค่าเริ่มต้นของ anchor provider คือ `ANCHOR_PROVIDER=auto`: ถ้ามี Surya/PaddleOCR จะใช้ได้ แต่ถ้ายังไม่ติดตั้งจะ fallback เป็น OpenCV header-line detector ที่มากับระบบ

## ข้อสรุปเรื่องความเร็ว

จาก log ล่าสุดบนเครื่องนี้:

- `02.pdf` 2 หน้า: OCR จริงใช้ประมาณ `24-30 วินาที`
  - page 1 ประมาณ `11.7-11.8s`
  - page 2 ประมาณ `12.3-12.4s`
- เอกสาร 2-3 หน้าแบบปกติควรอยู่ประมาณ `30-70 วินาที`
- ถ้าเกิน `3 นาที` มักไม่ใช่เพราะจำนวนหน้า แต่เป็นเพราะ endpoint timeout หรือ fallback/vision ค้าง

## งานที่ปรับล่าสุด

### `server/app/import_pipeline.py`

- ปรับ `IMPORT_OCR_PIPELINE_VERSION=67`
- ปรับ logic `ข้อความ พิพากษาให้จำเลย` ให้เก็บต่อจนเจอ end marker แบบ strict ที่ต้องมี `./` เท่านั้น (`ค่าใช้จ่ายในการดำเนินคดีให้เป็นพับ./`, `ให้เป็นพับ./`, `เป็นพับ./`, `พับ./`) แทนการหยุดด้วย cap 7 segment / 320 ตัวอักษรแบบเดิม
- เพิ่ม fallback เฉพาะกรณี OCR ทำ `./` หายหรือแยก `ให้เป็นพับ` เป็นหลาย segment เพื่อหยุดก่อนชื่อผู้พิพากษา/ลายเซ็น โดยไม่ใช้ `พับ` เดี่ยว ๆ เป็น marker
- เพิ่มการรองรับเอกสารแบบ `03.pdf` ที่คำพิพากษาเป็นคดีคืนรถ/ใช้ราคาแทน: อ่านยอดจาก phrase `ใช้ราคาแทนเป็นเงิน`, normalize เลขคดี `ผูE/ผE` กลับเป็น `ผบE`, ดึงวันฟ้องจากรูปแบบ `วันฟ้อง (วันที่ ...)`, และต่อข้อความพิพากษาข้ามหน้าเมื่อเริ่มหน้า 4 แต่ไปจบหน้า 5
- ปรับการ slice ข้อความ judgment ให้รวม end marker เข้าไปด้วย เพื่อให้ข้อความฝั่งขวาและกรอบ highlight ฝั่งซ้ายไม่ขาดบรรทัดท้าย
- เพิ่ม preflight health check สำหรับ OCR endpoint
- เพิ่ม preflight สำหรับ Vision endpoint และข้าม vision fallback ถ้า endpoint ไม่พร้อม
- เพิ่ม review data extraction ฝั่ง backend สำหรับ field สำคัญ เช่น:
  - คดีดำ
  - คดีแดง
  - ศาล
  - ยอดชำระ
  - ดอกเบี้ย
  - เงินต้น
  - วันที่เริ่มต้น/วันฟ้อง
  - ค่าทนายความ
- เพิ่ม targeted OCR เฉพาะ header ของหน้า 1
- อ่านคดีดำ/คดีแดงจาก crop เฉพาะบรรทัด แทนการพึ่ง full-page OCR อย่างเดียว
- ถ้าเลขคดีขึ้นต้น `ผบ` แล้วตามด้วยเลขทันที เช่น `ผบ๔๖๑๖/๒๕๖๗` จะถือว่าน่าสงสัย เพราะอาจทำตัว `E` หาย
- เพิ่ม logic restore เป็น `ผบE...` สำหรับเคสที่ OCR ทำ `E` หายจากเลขคดี
- ลด fallback ที่หนักเกินไป ไม่ให้เอกสารที่ไม่มีร่องรอยเลขคดีต้องวิ่ง crop recovery หนัก
- ผูก `review_data.fields.caseBlackNo/caseRedNo/courtName.bbox` ให้ใช้ bbox จาก anchor provider ก่อน fallback เก่า
- ทดสอบกับ `01.pdf` และ `02.pdf` แล้ว backend ได้ source เป็น `opencv_header_right_anchor` / `opencv_header_center_anchor`
- แก้เคส `03.pdf` หน้า 4 ที่กรอบสีส้มของข้อความพิพากษาเลื่อนสูงเกินจริง: ปรับ line-box assignment ให้ OCR line ที่ยาวมากจับ bbox หลายบรรทัดได้ถูกต้อง และ rebuild cache ได้โดยไม่ต้องยิง OCR ใหม่

### `server/app/anchor_provider.py`

- เพิ่ม anchor provider กลางสำหรับหา line bbox จาก preview image
- รองรับ provider:
  - `surya` ถ้าติดตั้ง `surya-ocr`
  - `paddle` ถ้าติดตั้ง `paddleocr` + `paddlepaddle`
  - `opencv` fallback ที่ไม่ต้องติดตั้ง model เพิ่ม
- โหมด `auto` จะลอง Surya -> PaddleOCR -> OpenCV ตามลำดับ
- OpenCV fallback ใช้จับบรรทัด header หน้า 1 สำหรับ:
  - คดีดำ
  - คดีแดง
  - ศาล
- เพิ่ม `server/requirements-anchor-optional.txt` สำหรับ dependency เสริมของ Surya แต่ยังไม่บังคับติดตั้ง เพราะดึง `torch/transformers` และอาจหนักบน Windows

### `server/app/config.py`

- เพิ่มค่า config:
  - `ANCHOR_PROVIDER` default `auto`
  - `ANCHOR_LANGUAGE` default `th`

### `client/app/imports-new/[importId]/page.tsx`

- ปรับ field navigation ให้ใช้ backend bbox/detected header anchor ก่อนการเดาแบบ fallback
- ปรับลำดับเลือก anchor ของ field หัวเอกสารให้ใช้ bbox จาก backend ก่อน detector จากรูป และขยายช่วง valid ของ `courtName` เพื่อให้เอกสารแบบ `02.pdf` ที่ตำแหน่งศาลอยู่สูงกว่า template เดิมไม่โดน detector จับผิดไปที่คำว่า `จำเลย`
- เพิ่ม detector ฝั่ง frontend จาก preview image เพื่อช่วยเอกสารเก่าที่ยังไม่ได้รัน pipeline version 61
- จำกัดการคลิกเลื่อน preview เฉพาะ `คดีแดง`, `คดีดำ`, `ศาล`; field อื่นยังแก้ไขด้วย `EDIT` ได้ แต่ไม่เลื่อนกรอบฝั่งซ้าย
- กรอบ highlight ปรับเป็น capsule สีส้มตามบรรทัด เพื่ออ่านตัวอักษรง่ายขึ้น

### `server/app/watermark_cleaner.py`

- เพิ่ม `clean_pil_image_soft`
- เพิ่ม soft-clean mode สำหรับ header/case-number crop
- soft-clean ใช้กับคดีดำ/คดีแดงหน้า 1 เพื่อรักษาตัวอักษรบาง ๆ เช่น `E`
- normal clean ยังใช้สำหรับภาพทั้งหน้าเหมือนเดิม

### `server/app/typhoon.py`

- หน้าแรกสามารถลอง original candidate เฉพาะกรณี cleaned OCR อาจทำ Latin case series หาย
- เพิ่มตัวตรวจ case number ที่มี Latin series เช่น `E4616/2567`
- ลดการลอง candidate ซ้ำเมื่อ cleaned OCR ล้มเหลว เพื่อไม่ให้เสียเวลา timeout หลายรอบ
- ยังใช้ cleaned OCR เป็นแหล่งหลักสำหรับเนื้อหา เพราะ original มี watermark รบกวนหนัก

## ผลทดสอบเคส `01.pdf`

ปัญหาเดิม:

- เอกสารจริงมีเลขคดีดำ `ผบE๔๖๑๖/๒๕๖๗`
- OCR full-page จาก cleaned image อ่านเป็น `ผบ๔๖๑๖/๒๕๖๗`
- frontend ไม่ได้ตัด `E`; ค่า `E` หายตั้งแต่ backend/OCR layer

ผลหลังแก้:

- คดีดำ: `ผบE๔๖๑๖/๒๕๖๗`
- คดีแดง: `ผบE๕๐๙๙/๒๕๖๗`
- API ล่าสุดคืนค่า `ready_for_review`

## ผลทดสอบเคส `03.pdf`

ปัญหาเดิม:

- หน้า 4 มี OCR line ย่อหน้าก่อนคำพิพากษายาวมาก ทำให้ segment/bbox ของคำว่า `พิพากษาให้จำเลย...` ถูกเลื่อนไปอยู่สูงกว่าตำแหน่งจริง
- ฝั่งขวา slice ข้อความถูกแล้ว แต่กรอบสีส้มฝั่ง preview ครอบย่อหน้าก่อนหน้าผิด

ผลหลังแก้:

- `03.pdf` ถูก rebuild จาก OCR cache เดิมเป็น pipeline version `68`
- bbox ของ `page-4-judgment-1` เปลี่ยนจากประมาณ `top=0.444` เป็น `top=0.669` ทำให้กรอบเริ่มที่บรรทัด `พิพากษาให้จำเลย...` ตามภาพจริง
- `page-5-judgment-continuation-1` ยังทำงานต่อจากหน้า 5 เหมือนเดิม
- แก้ข้อความฝั่งขวาของ page 5 ไม่ให้ใช้ segment text ที่แตกคำจาก bbox alignment โดยใช้ OCR markdown ดิบเป็น display text แทน แล้ว normalize spacing เฉพาะคำสำคัญ เช่น `เป็นต้นไป`, `ค่าขาดประโยชน์`, `ค่าใช้จ่ายในการดำเนินคดี`

## ผลทดสอบเคส `06112024140950.pdf`

ปัญหาเดิม:

- OCR markdown หลักบางครั้งอ่านเลขคดีเป็น `ผบ๔๑๕๒/๒๕๖๗` และ `ผบ๔๙๒๙/๒๕๖๗` โดยทำ `E` หาย
- เลขคดีในระบบนี้ต้องมี series `E` หลัง `ผบ` ทุกเคส จึงต้อง restore เป็น `ผบE...`

ผลหลังแก้:

- Pipeline version ล่าสุดเป็น `71`
- คืน rule restore `ผบ` + ตัวเลข เป็น `ผบE...`
- ยัง normalize เคส OCR เพี้ยนแบบ `ผูE/ผE` กลับเป็น `ผบE` ได้เหมือนเดิม
- Rebuild cache ของชุดไฟล์ `0611202414*.pdf` แล้ว: `06112024140950.pdf` ได้คดีดำ `ผบE๔๑๕๒/๒๕๖๗` และคดีแดง `ผบE๔๙๒๙/๒๕๖๗`
- แก้กรอบส้มของข้อความ `พิพากษาให้จำเลย` ที่เลื่อนไปครอบส่วนคู่ความ: OCR ของไฟล์นี้สร้างบรรทัด `ผู้พิพากษา` ซ้ำจำนวนมากจากตราประทับ/ลายเซ็น ทำให้ line-box alignment จับกล่องผิด จึงกรองบรรทัด noise นี้ก่อนสร้าง segment
- Rebuild `06112024140950.pdf` แล้ว bbox ของ `page-1-judgment-1` ขยับจากประมาณ `top=0.291` เป็น `top=0.631` ตรงย่อหน้าพิพากษาด้านล่างมากขึ้น

## ข้อสรุปเรื่องลายน้ำ

- การ clean ทั้งหน้าช่วย OCR โดยรวม แต่บางครั้งทำให้ตัวอักษรบาง เช่น `E` ในเลขคดีหาย
- original image มีลายน้ำเยอะจน OCR header กว้างอ่านผิดได้
- วิธีที่ดีที่สุดตอนนี้คือ:
  - ใช้ cleaned image สำหรับ OCR หลัก
  - ใช้ crop เฉพาะบรรทัดคดีดำ/คดีแดงของหน้า 1
  - ใช้ soft-clean เฉพาะ header/case-number crop
  - ไม่ลดความแรงของ watermark cleaning ทั้งหน้า เพราะอาจทำให้หน้าอื่นแย่ลง

## สถานะ Qwen/Vision

- ตอนนี้ปิด Qwen/Vision ออกจากระบบก่อน
- [server/.env](/c:/Users/waruen.w/Desktop/ocrnew/server/.env) ตั้ง `VISION_BASE_URL=` และ `VISION_MODEL=`
- [server/app/config.py](/c:/Users/waruen.w/Desktop/ocrnew/server/app/config.py) ตั้ง default vision เป็นค่าว่าง เพื่อไม่ fallback ไป Qwen local เอง
- `Settings.vision_ready` ถูกบังคับให้เป็น `False` เสมอ ดังนั้นต่อให้มีค่า `VISION_BASE_URL` และ `VISION_MODEL` ก็จะไม่เรียก Qwen
- ถ้าจะเปิดกลับมาใช้ debug/manual fallback ต้องแก้ `Settings.vision_ready` กลับก่อน แล้วค่อยใส่ `VISION_BASE_URL` และ `VISION_MODEL`

## หมายเหตุ

- ถ้า OCR ค้างเกินปกติ ให้เช็ก endpoint `http://10.25.99.23:8200`
- ถ้าเปลี่ยน `.env` ต้อง restart backend
- ถ้าลบข้อมูลแล้วอัปโหลดใหม่ เอกสารใหม่จะใช้ pipeline version ล่าสุด
- ถ้าต้องการเปิดใช้ Surya จริง ให้ติดตั้ง `server/requirements-anchor-optional.txt` แล้ว restart backend; ตอนนี้ระบบยังใช้ OpenCV fallback ได้โดยไม่ต้องติดตั้ง model เพิ่ม
