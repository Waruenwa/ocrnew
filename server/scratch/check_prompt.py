import os
from pathlib import Path
from typhoon_ocr.ocr_utils import prepare_ocr_messages

# Mocking some parameters
dummy_path = "01.pdf" # Doesn't need to exist for just checking the prompt generation if it's string based, but library might check
if not os.path.exists(dummy_path):
    with open(dummy_path, "wb") as f:
        f.write(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF")

messages = prepare_ocr_messages(
    pdf_or_image_path=dummy_path,
    task_type="default",
    target_image_dim=900,
    page_num=1,
    figure_language="Thai",
)

print(messages)
