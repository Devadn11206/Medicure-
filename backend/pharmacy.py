import sqlite3
import math
from typing import List, Dict, Optional, Tuple

# Bengaluru pharmacy locations (15 major chains with realistic coordinates)
PHARMACY_DATA = [
    {"name": "Apollo Pharmacy - Indiranagar", "address": "126, 100 ft Road, Indiranagar, Bengaluru", "lat": 12.9718, "lng": 77.6412},
    {"name": "MedPlus - Jayanagar", "address": "45, 11th Main, Jayanagar, Bengaluru", "lat": 12.9349, "lng": 77.5943},
    {"name": "Netmeds - Koramangala", "address": "456, 4th Block, Koramangala, Bengaluru", "lat": 12.9354, "lng": 77.6248},
    {"name": "1mg Pharmacy - Whitefield", "address": "789, Whitefield Main Road, Bengaluru", "lat": 12.9700, "lng": 77.7484},
    {"name": "Wellness Forever - MG Road", "address": "123, MG Road, Bengaluru", "lat": 12.9721, "lng": 77.6068},
    {"name": "Frank Ross - Bommanahalli", "address": "234, Bommanahalli, Bengaluru", "lat": 12.9226, "lng": 77.6114},
    {"name": "Guardian Pharmacy - BTM Layout", "address": "567, BTM Layout, Bengaluru", "lat": 12.9218, "lng": 77.6065},
    {"name": "Pharmeasy - HSR Layout", "address": "890, HSR Layout, Bengaluru", "lat": 12.9116, "lng": 77.6412},
    {"name": "Medlife - Marathahalli", "address": "345, Marathahalli, Bengaluru", "lat": 12.9548, "lng": 77.7004},
    {"name": "Care Pharmacy - Yeshwanthpur", "address": "678, Yeshwanthpur, Bengaluru", "lat": 13.0295, "lng": 77.5612},
    {"name": "Cochin Pharmacy - Vijayanagar", "address": "123, Vijayanagar, Bengaluru", "lat": 12.9643, "lng": 77.5455},
    {"name": "Apollo Pharmacy - BTM", "address": "456, BTM Layout, Bengaluru", "lat": 12.9217, "lng": 77.6058},
    {"name": "MedPlus - Whitefield", "address": "789, Whitefield Main Road, Bengaluru", "lat": 12.9715, "lng": 77.7489},
    {"name": "Netmeds - Indiranagar", "address": "234, Indiranagar, Bengaluru", "lat": 12.9696, "lng": 77.6391},
    {"name": "Wellness Forever - Koramangala", "address": "567, Koramangala, Bengaluru", "lat": 12.9371, "lng": 77.6239},
]

# Common medicines with price variations (±20% across pharmacies)
MEDICINE_BASE_PRICES = {
    "Augmentin 625": 120,
    "Crocin 650": 35,
    "Omez 20": 45,
    "Paracetamol 500": 25,
    "Aspirin 75": 30,
    "Ibuprofen 400": 40,
    "Metformin 500": 50,
    "Warfarin 5": 60,
    "Amoxicillin 500": 80,
    "Ciprofloxacin 500": 100,
    "Atorvastatin 10": 55,
    "Lisinopril 10": 65,
    "Amlodipine 5": 35,
    "Salbutamol": 85,
    "Doxycycline 100": 70,
    "Cetirizine 10": 25,
    "Loratadine 10": 30,
    "Fluticasone": 95,
    "Ranitidine 150": 20,
    "Metoprolol 50": 45,
}


def haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Calculate great-circle distance between two points on Earth.
    Returns distance in kilometers.
    """
    R = 6371  # Earth radius in km
    
    # Convert to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lng = math.radians(lng2 - lng1)
    
    # Haversine formula
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng / 2) ** 2
    distance = 2 * R * math.asin(math.sqrt(a))
    
    return round(distance, 2)


def get_pharmacy_price_variant(base_price: float, pharmacy_id: int) -> int:
    """
    Generate ±20% price variation for a medicine at a given pharmacy.
    Uses pharmacy_id as seed for consistency.
    """
    # Use pharmacy_id to create a deterministic offset (±20%)
    variance = ((pharmacy_id * 7) % 40) - 20  # Range: -20 to 19 (percent)
    varied_price = base_price * (1 + variance / 100)
    return int(round(varied_price))


def process_pharmacy_request(
    medicines: List[str],
    user_lat: Optional[float] = None,
    user_lng: Optional[float] = None,
    db_path: str = "backend/medicines.db"
) -> Dict:
    """
    Main logic to find best pharmacy that minimizes cost and distance.
    
    Args:
        medicines: List of medicine names to find
        user_lat: User's latitude (optional)
        user_lng: User's longitude (optional)
        db_path: Path to SQLite database
    
    Returns:
        Dict with recommended pharmacy and ranked list
    """
    
    if not medicines:
        return {"error": "Medicines list is empty"}
    
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    try:
        # Get all pharmacies
        cur.execute("SELECT pharmacy_id, name, address, lat, lng FROM pharmacy")
        pharmacies = cur.fetchall()
        
        if not pharmacies:
            return {"error": "No pharmacies available"}
        
        results = []
        
        for pharmacy in pharmacies:
            pharmacy_id = pharmacy["pharmacy_id"]
            pharmacy_name = pharmacy["name"]
            address = pharmacy["address"]
            pharm_lat = pharmacy["lat"]
            pharm_lng = pharmacy["lng"]
            
            # Calculate distance
            distance_km = 0
            if user_lat and user_lng:
                distance_km = haversine(user_lat, user_lng, pharm_lat, pharm_lng)
            
            # Get medicine prices and availability
            available = []
            unavailable = []
            total_cost = 0
            
            for medicine in medicines:
                # Query database for medicine at this pharmacy
                cur.execute(
                    "SELECT price_inr, in_stock FROM pharmacy_inventory WHERE pharmacy_id = ? AND medicine_name = ?",
                    (pharmacy_id, medicine)
                )
                inv = cur.fetchone()
                
                if inv and inv["in_stock"] == 1:
                    available.append(medicine)
                    total_cost += inv["price_inr"]
                else:
                    unavailable.append(medicine)
            
            # Skip pharmacy if >50% medicines unavailable
            availability_ratio = len(available) / len(medicines)
            if availability_ratio < 0.5:
                continue
            
            # Calculate score: cost + distance penalty
            # 1km = ₹10 penalty in cost
            score = total_cost + (distance_km * 10)
            
            results.append({
                "pharmacy_id": pharmacy_id,
                "pharmacy_name": pharmacy_name,
                "address": address,
                "distance_km": distance_km,
                "total_medicine_cost": total_cost,
                "score": round(score, 2),
                "medicines_available": available,
                "medicines_unavailable": unavailable,
                "availability_ratio": availability_ratio,
            })
        
        # Sort by score ascending (best = lowest score)
        results.sort(key=lambda x: x["score"])
        
        if not results:
            return {"error": "No pharmacies have sufficient medicine availability"}
        
        # Get top 3 recommendations
        top_3 = results[:3]
        worst_score = results[-1]["score"]
        savings = worst_score - top_3[0]["score"]
        
        response = {
            "recommended": {
                "pharmacy_name": top_3[0]["pharmacy_name"],
                "address": top_3[0]["address"],
                "distance_km": top_3[0]["distance_km"],
                "total_medicine_cost": top_3[0]["total_medicine_cost"],
                "score": top_3[0]["score"],
                "medicines_available": top_3[0]["medicines_available"],
                "medicines_unavailable": top_3[0]["medicines_unavailable"],
            },
            "all_pharmacies": [
                {
                    "pharmacy_name": p["pharmacy_name"],
                    "address": p["address"],
                    "distance_km": p["distance_km"],
                    "total_medicine_cost": p["total_medicine_cost"],
                    "score": p["score"],
                    "medicines_available_count": len(p["medicines_available"]),
                    "medicines_unavailable_count": len(p["medicines_unavailable"]),
                }
                for p in top_3
            ],
            "savings_vs_worst": round(savings, 2),
            "total_pharmacies_evaluated": len(results),
        }
        
        return response
    
    finally:
        conn.close()


def get_all_pharmacies(db_path: str = "backend/medicines.db") -> List[Dict]:
    """
    Retrieve all pharmacies from database for frontend map display.
    """
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT pharmacy_id, name, address, lat, lng FROM pharmacy")
        pharmacies = cur.fetchall()
        
        return [
            {
                "pharmacy_id": p["pharmacy_id"],
                "name": p["name"],
                "address": p["address"],
                "lat": p["lat"],
                "lng": p["lng"],
            }
            for p in pharmacies
        ]
    finally:
        conn.close()
