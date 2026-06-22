import json
import os
import sys

# Ensure backend module can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.database import engine, Base, SessionLocal
from backend.models import Medicine

# Hardcoded seed data for Indian medicines
SEED_DATA = [
    {"medicine_name": "Crocin 650", "active_ingredient": "Paracetamol", "generic_name": "Paracetamol 650mg", "brand_price": 60, "generic_price": 20, "category": "Analgesic"},
    {"medicine_name": "Dolo 650", "active_ingredient": "Paracetamol", "generic_name": "Paracetamol 650mg", "brand_price": 65, "generic_price": 20, "category": "Analgesic"},
    {"medicine_name": "Augmentin 625", "active_ingredient": "Amoxicillin + Clavulanic Acid", "generic_name": "Generic Amox-Clav", "brand_price": 220, "generic_price": 90, "category": "Antibiotic"},
    {"medicine_name": "Glucophage", "active_ingredient": "Metformin", "generic_name": "Metformin 500mg", "brand_price": 180, "generic_price": 60, "category": "Anti-diabetic"},
    {"medicine_name": "Glycomet 500", "active_ingredient": "Metformin", "generic_name": "Metformin 500mg", "brand_price": 150, "generic_price": 60, "category": "Anti-diabetic"},
    {"medicine_name": "Amlokind 5", "active_ingredient": "Amlodipine", "generic_name": "Amlodipine 5mg", "brand_price": 45, "generic_price": 15, "category": "Anti-hypertensive"},
    {"medicine_name": "Amlong 5", "active_ingredient": "Amlodipine", "generic_name": "Amlodipine 5mg", "brand_price": 50, "generic_price": 15, "category": "Anti-hypertensive"},
    {"medicine_name": "Telma 40", "active_ingredient": "Telmisartan", "generic_name": "Telmisartan 40mg", "brand_price": 140, "generic_price": 40, "category": "Anti-hypertensive"},
    {"medicine_name": "Tazloc 40", "active_ingredient": "Telmisartan", "generic_name": "Telmisartan 40mg", "brand_price": 120, "generic_price": 40, "category": "Anti-hypertensive"},
    {"medicine_name": "Pan 40", "active_ingredient": "Pantoprazole", "generic_name": "Pantoprazole 40mg", "brand_price": 160, "generic_price": 35, "category": "Antacid"},
    {"medicine_name": "Pantocid 40", "active_ingredient": "Pantoprazole", "generic_name": "Pantoprazole 40mg", "brand_price": 155, "generic_price": 35, "category": "Antacid"},
    {"medicine_name": "Omez 20", "active_ingredient": "Omeprazole", "generic_name": "Omeprazole 20mg", "brand_price": 80, "generic_price": 25, "category": "Antacid"},
    {"medicine_name": "Omee", "active_ingredient": "Omeprazole", "generic_name": "Omeprazole 20mg", "brand_price": 75, "generic_price": 25, "category": "Antacid"},
    {"medicine_name": "Allegra 120", "active_ingredient": "Fexofenadine", "generic_name": "Fexofenadine 120mg", "brand_price": 200, "generic_price": 60, "category": "Antihistamine"},
    {"medicine_name": "Fexofen 120", "active_ingredient": "Fexofenadine", "generic_name": "Fexofenadine 120mg", "brand_price": 180, "generic_price": 60, "category": "Antihistamine"},
    {"medicine_name": "Cetiriz", "active_ingredient": "Cetirizine", "generic_name": "Cetirizine 10mg", "brand_price": 30, "generic_price": 10, "category": "Antihistamine"},
    {"medicine_name": "Cetzine 10", "active_ingredient": "Cetirizine", "generic_name": "Cetirizine 10mg", "brand_price": 35, "generic_price": 10, "category": "Antihistamine"},
    {"medicine_name": "Okacet 10", "active_ingredient": "Cetirizine", "generic_name": "Cetirizine 10mg", "brand_price": 32, "generic_price": 10, "category": "Antihistamine"},
    {"medicine_name": "Taxim O 200", "active_ingredient": "Cefixime", "generic_name": "Cefixime 200mg", "brand_price": 180, "generic_price": 70, "category": "Antibiotic"},
    {"medicine_name": "Zifi 200", "active_ingredient": "Cefixime", "generic_name": "Cefixime 200mg", "brand_price": 160, "generic_price": 70, "category": "Antibiotic"},
    {"medicine_name": "Monocef O 200", "active_ingredient": "Cefpodoxime", "generic_name": "Cefpodoxime 200mg", "brand_price": 250, "generic_price": 90, "category": "Antibiotic"},
    {"medicine_name": "Macpod 200", "active_ingredient": "Cefpodoxime", "generic_name": "Cefpodoxime 200mg", "brand_price": 240, "generic_price": 90, "category": "Antibiotic"},
    {"medicine_name": "Azithral 500", "active_ingredient": "Azithromycin", "generic_name": "Azithromycin 500mg", "brand_price": 140, "generic_price": 50, "category": "Antibiotic"},
    {"medicine_name": "Azee 500", "active_ingredient": "Azithromycin", "generic_name": "Azithromycin 500mg", "brand_price": 130, "generic_price": 50, "category": "Antibiotic"},
    {"medicine_name": "Bactrim DS", "active_ingredient": "Sulfamethoxazole + Trimethoprim", "generic_name": "Co-trimoxazole", "brand_price": 40, "generic_price": 15, "category": "Antibiotic"},
    {"medicine_name": "Septran DS", "active_ingredient": "Sulfamethoxazole + Trimethoprim", "generic_name": "Co-trimoxazole", "brand_price": 45, "generic_price": 15, "category": "Antibiotic"},
    {"medicine_name": "Thyronorm 50", "active_ingredient": "Thyroxine", "generic_name": "Levothyroxine 50mcg", "brand_price": 160, "generic_price": 50, "category": "Hormone"},
    {"medicine_name": "Eltroxin 50", "active_ingredient": "Thyroxine", "generic_name": "Levothyroxine 50mcg", "brand_price": 150, "generic_price": 50, "category": "Hormone"},
    {"medicine_name": "Voveran SR 100", "active_ingredient": "Diclofenac", "generic_name": "Diclofenac 100mg", "brand_price": 180, "generic_price": 40, "category": "NSAID"},
    {"medicine_name": "Reactin SR 100", "active_ingredient": "Diclofenac", "generic_name": "Diclofenac 100mg", "brand_price": 160, "generic_price": 40, "category": "NSAID"},
    {"medicine_name": "Brufen 400", "active_ingredient": "Ibuprofen", "generic_name": "Ibuprofen 400mg", "brand_price": 30, "generic_price": 12, "category": "NSAID"},
    {"medicine_name": "Combiflam", "active_ingredient": "Ibuprofen + Paracetamol", "generic_name": "Ibuprofen-Paracetamol", "brand_price": 40, "generic_price": 15, "category": "NSAID"},
    {"medicine_name": "Imodium", "active_ingredient": "Loperamide", "generic_name": "Loperamide 2mg", "brand_price": 50, "generic_price": 15, "category": "Anti-diarrheal"},
    {"medicine_name": "Lomotil", "active_ingredient": "Diphenoxylate + Atropine", "generic_name": "Diphenoxylate-Atropine", "brand_price": 30, "generic_price": 10, "category": "Anti-diarrheal"},
    {"medicine_name": "Ecosprin 75", "active_ingredient": "Aspirin", "generic_name": "Aspirin 75mg", "brand_price": 20, "generic_price": 5, "category": "Anti-platelet"},
    {"medicine_name": "Disprin", "active_ingredient": "Aspirin", "generic_name": "Aspirin 325mg", "brand_price": 15, "generic_price": 6, "category": "Analgesic"},
    {"medicine_name": "Lipicard 160", "active_ingredient": "Fenofibrate", "generic_name": "Fenofibrate 160mg", "brand_price": 200, "generic_price": 60, "category": "Lipid-lowering"},
    {"medicine_name": "TGR 160", "active_ingredient": "Fenofibrate", "generic_name": "Fenofibrate 160mg", "brand_price": 180, "generic_price": 60, "category": "Lipid-lowering"},
    {"medicine_name": "Atorva 20", "active_ingredient": "Atorvastatin", "generic_name": "Atorvastatin 20mg", "brand_price": 180, "generic_price": 50, "category": "Statin"},
    {"medicine_name": "Storvas 20", "active_ingredient": "Atorvastatin", "generic_name": "Atorvastatin 20mg", "brand_price": 190, "generic_price": 50, "category": "Statin"},
    {"medicine_name": "Aztor 20", "active_ingredient": "Atorvastatin", "generic_name": "Atorvastatin 20mg", "brand_price": 170, "generic_price": 50, "category": "Statin"},
    {"medicine_name": "Rosuvas 10", "active_ingredient": "Rosuvastatin", "generic_name": "Rosuvastatin 10mg", "brand_price": 220, "generic_price": 70, "category": "Statin"},
    {"medicine_name": "Creastor 10", "active_ingredient": "Rosuvastatin", "generic_name": "Rosuvastatin 10mg", "brand_price": 240, "generic_price": 70, "category": "Statin"},
    {"medicine_name": "Novamox 500", "active_ingredient": "Amoxicillin", "generic_name": "Amoxicillin 500mg", "brand_price": 120, "generic_price": 40, "category": "Antibiotic"},
    {"medicine_name": "Almox 500", "active_ingredient": "Amoxicillin", "generic_name": "Amoxicillin 500mg", "brand_price": 110, "generic_price": 40, "category": "Antibiotic"},
    {"medicine_name": "Erythromycin 500", "active_ingredient": "Erythromycin", "generic_name": "Erythromycin 500mg", "brand_price": 150, "generic_price": 60, "category": "Antibiotic"},
    {"medicine_name": "Ascoril", "active_ingredient": "Bromhexine + Guaifenesin", "generic_name": "Cough Syrup Generic", "brand_price": 120, "generic_price": 45, "category": "Cough Syrup"},
    {"medicine_name": "Corex", "active_ingredient": "Chlorpheniramine + Codeine", "generic_name": "Codeine Cough Syrup", "brand_price": 150, "generic_price": 50, "category": "Cough Syrup"},
    {"medicine_name": "Deriphyllin", "active_ingredient": "Etofylline + Theophylline", "generic_name": "Theophylline Generic", "brand_price": 40, "generic_price": 15, "category": "Bronchodilator"},
    {"medicine_name": "Asthalin 100mcg", "active_ingredient": "Salbutamol", "generic_name": "Salbutamol Inhaler", "brand_price": 180, "generic_price": 70, "category": "Bronchodilator"}
]

def seed_db():
    print("Creating tables if not exist...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        if db.query(Medicine).count() == 0:
            print(f"Seeding {len(SEED_DATA)} medicines...")
            for data in SEED_DATA:
                med = Medicine(**data)
                db.add(med)
            db.commit()
            print("Seeding complete.")
        else:
            print("Database already seeded with medicines.")
    except Exception as e:
        print(f"Error seeding DB: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_db()
