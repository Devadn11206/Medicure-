import asyncio
from backend.ocr_service import process_document

try:
    with open("dummy.jpg", "wb") as f:
        f.write(b"dummy")
    with open("dummy.jpg", "rb") as f:
        process_document(f.read(), "dummy.jpg")
except Exception as e:
    import traceback
    traceback.print_exc()
