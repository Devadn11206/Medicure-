import os
import json
import io
import fitz  # PyMuPDF
import easyocr
import pydantic
from PIL import Image
import numpy as np
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pathlib import Path

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

# Initialize EasyOCR reader once globally to save time
reader = easyocr.Reader(['en'], gpu=False)

def _pdf_to_image_bytes(pdf_bytes: bytes) -> bytes:
    """Convert the first page of a PDF to an image byte array."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    if doc.page_count == 0:
        raise ValueError("PDF has no pages")
    page = doc.load_page(0)
    pix = page.get_pixmap()
    img_data = pix.tobytes("png")
    doc.close()
    return img_data

def process_document(file_bytes: bytes, filename: str) -> dict:
    # 1. Handle PDF vs Image
    if filename.lower().endswith('.pdf'):
        image_bytes = _pdf_to_image_bytes(file_bytes)
    else:
        image_bytes = file_bytes
    
    # 2. Extract Text with EasyOCR
    # EasyOCR can read from bytes directly
    ocr_results = reader.readtext(image_bytes)
    ocr_text = "\n".join([result[1] for result in ocr_results])
    
    if not ocr_text.strip():
        raise ValueError("No text could be extracted from the document.")

    # 3. Process with Gemini
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "your_key_here":
        raise ValueError("Valid GEMINI_API_KEY is missing in .env")
    
    client = genai.Client(api_key=api_key)
    
    prompt = f"""You are an expert medical prescription parser.
Analyze OCR text extracted from a doctor's prescription.

Tasks:
1. Extract medicine names.
2. Extract dosage.
3. Extract frequency.
4. Extract duration if available.
5. Ignore doctor names, hospital names, addresses, signatures and unrelated text.
6. Correct common OCR mistakes.
7. Return valid JSON only.

Output Format:
{{
  "medicines": [
    {{
      "medicine_name": "",
      "dosage": "",
      "frequency": "",
      "duration": ""
    }}
  ]
}}

OCR Text:
{ocr_text}
"""
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1
        )
    )
    
    try:
        response_json = json.loads(response.text)
    except Exception as e:
        raise ValueError(f"Failed to parse Gemini output as JSON: {e}")
        
    medicines = response_json.get("medicines", [])
    
    return {
        "success": True,
        "ocr_text": ocr_text,
        "medicine_count": len(medicines),
        "medicines": medicines
    }
