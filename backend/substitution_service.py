import os
from google import genai
from google.genai import types
from sqlalchemy.orm import Session
from sqlalchemy import func
from .models import Medicine
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=env_path)

def generate_explanation(brand_name: str, generic_name: str, active_ingredient: str) -> str:
    """Uses Gemini to explain why the generic is a suitable alternative."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or api_key == "your_key_here":
        return f"{generic_name} contains the same active ingredient ({active_ingredient}) as {brand_name} and provides similar benefits at a lower cost."

    client = genai.Client(api_key=api_key)
    
    prompt = f"""
    Explain in simple language why the suggested generic medicine is a suitable alternative.
    Brand Medicine: {brand_name}
    Generic Alternative: {generic_name}
    Active Ingredient: {active_ingredient}
    
    Maximum 50 words.
    """
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3
            )
        )
        return response.text.strip()
    except Exception as e:
        # Fallback explanation if API fails
        return f"{generic_name} contains the same active ingredient ({active_ingredient}) as {brand_name} and provides similar relief at a significantly lower cost."


def check_substitutions(medicine_names: list[str], db: Session) -> dict:
    results = []
    monthly_savings = 0
    
    for med_name in medicine_names:
        med_name_lower = med_name.strip().lower()
        if not med_name_lower:
            continue
            
        # Try exact match first
        med = db.query(Medicine).filter(func.lower(Medicine.medicine_name) == med_name_lower).first()
        
        # Fallback to contains match if not found exactly
        if not med:
            med = db.query(Medicine).filter(func.lower(Medicine.medicine_name).like(f"%{med_name_lower}%")).first()
            
        if med:
            savings = max(0, med.brand_price - med.generic_price)
            if savings > 0:
                explanation = generate_explanation(med.medicine_name, med.generic_name, med.active_ingredient)
                monthly_savings += savings
                
                results.append({
                    "medicine": med.medicine_name,
                    "active_ingredient": med.active_ingredient,
                    "alternative": med.generic_name,
                    "brand_price": med.brand_price,
                    "generic_price": med.generic_price,
                    "savings": savings,
                    "explanation": explanation
                })

    annual_savings = monthly_savings * 12
    
    return {
        "success": True,
        "results": results,
        "monthly_savings": monthly_savings,
        "annual_savings": annual_savings,
        "medicines_analyzed": len(medicine_names),
        "alternatives_found": len(results)
    }
