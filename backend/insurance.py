from typing import Any, Dict, List, Optional

INSURANCE_PLANS = {
    "star_health_comprehensive": {
        "display_name": "Star Health Comprehensive",
        "sum_insured": 500000,
        "room_rent_limit": 10000,
        "copay_percent": 0,
        "deductible": 0,
        "medicine_copay": 20,
        "pre_existing_waiting": 48,
        "excluded_categories": ["cosmetic", "dental", "vision"],
        "surgery_covered": True,
        "diagnostics_covered": True,
    },
    "hdfc_ergo_optima": {
        "display_name": "HDFC Ergo Optima",
        "sum_insured": 300000,
        "room_rent_limit": 5000,
        "copay_percent": 20,
        "deductible": 2500,
        "medicine_copay": 0,
        "pre_existing_waiting": 36,
        "excluded_categories": ["cosmetic", "dental"],
        "surgery_covered": True,
        "diagnostics_covered": True,
    },
    "niva_bupa_reassure": {
        "display_name": "Niva Bupa ReAssure",
        "sum_insured": 1000000,
        "room_rent_limit": None,
        "copay_percent": 0,
        "deductible": 0,
        "medicine_copay": 0,
        "pre_existing_waiting": 24,
        "excluded_categories": ["cosmetic"],
        "surgery_covered": True,
        "diagnostics_covered": True,
    },
    "care_health_care500": {
        "display_name": "Care Health Care500",
        "sum_insured": 500000,
        "room_rent_limit": 7500,
        "copay_percent": 10,
        "deductible": 0,
        "medicine_copay": 10,
        "pre_existing_waiting": 36,
        "excluded_categories": ["cosmetic", "dental", "vision"],
        "surgery_covered": True,
        "diagnostics_covered": True,
    },
    "no_insurance": {
        "display_name": "No Insurance",
        "sum_insured": 0,
        "room_rent_limit": None,
        "copay_percent": 0,
        "deductible": 0,
        "medicine_copay": 100,
        "pre_existing_waiting": 0,
        "excluded_categories": [],
        "surgery_covered": False,
        "diagnostics_covered": False,
    },
}

RECOGNIZED_CATEGORIES = {
    "room",
    "surgery",
    "medicine",
    "diagnostics",
    "cosmetic",
    "dental",
    "vision",
    "other",
}


def gemini_classify_category(item_name: str, amount: float) -> str:
    """
    Fallback classification for unrecognized bill items.

    This implementation does not call an external Gemini API.
    Instead, it defaults to 'other', which is covered at 50%.
    """
    return "other"


def process_insurance_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    total_bill = payload.get("total_bill")
    treatment_type = payload.get("treatment_type")
    plan_key = payload.get("insurance_plan")
    items = payload.get("items", [])

    if not total_bill or total_bill <= 0:
        return {"error": "total_bill must be greater than 0"}

    if not isinstance(items, list) or len(items) == 0:
        return {"error": "items list is required"}

    if plan_key not in INSURANCE_PLANS:
        valid_plans = list(INSURANCE_PLANS.keys())
        return {
            "error": "Unknown insurance plan",
            "valid_plans": valid_plans,
        }

    plan = INSURANCE_PLANS[plan_key]
    warnings: List[str] = []
    breakdown: List[Dict[str, Any]] = []
    total_covered = 0.0
    total_patient_pays = 0.0

    for raw_item in items:
        name = raw_item.get("name", "Unknown item")
        amount = float(raw_item.get("amount", 0))
        category = (raw_item.get("category") or "").strip().lower()

        if category not in RECOGNIZED_CATEGORIES:
            category = gemini_classify_category(name, amount)
            warnings.append(
                f"The item '{name}' could not be classified automatically, defaulting to '{category}'."
            )

        covered = 0.0
        patient_pays = 0.0
        reason = ""

        if plan_key == "no_insurance":
            covered = 0.0
            patient_pays = amount
            reason = "No insurance coverage available."
        elif category == "room":
            limit = plan["room_rent_limit"]
            if limit is None:
                covered = amount
                patient_pays = 0.0
                reason = "Room charges fully covered under this plan."
            else:
                covered = min(amount, limit)
                patient_pays = amount - covered
                reason = (
                    f"Room rent capped at ₹{limit:,} per day under this plan"
                    if patient_pays > 0
                    else "Room charges are fully covered within the daily limit."
                )
                if patient_pays > 0:
                    warnings.append(
                        f"Room charges exceed plan's daily room rent limit of ₹{limit:,}."
                    )
        elif category == "surgery":
            if plan["surgery_covered"]:
                covered = amount
                patient_pays = 0.0
                reason = "Fully covered under surgical benefit."
            else:
                covered = 0.0
                patient_pays = amount
                reason = "Surgery is not covered under this plan."
        elif category == "medicine":
            medicine_copay = plan["medicine_copay"]
            covered = amount * (1 - medicine_copay / 100)
            patient_pays = amount - covered
            reason = (
                f"{medicine_copay}% copay applies on medicine charges."
                if medicine_copay > 0
                else "Medicines are fully covered."
            )
        elif category == "diagnostics":
            if plan["diagnostics_covered"]:
                covered = amount
                patient_pays = 0.0
                reason = "Fully covered."
            else:
                covered = 0.0
                patient_pays = amount
                reason = "Diagnostics are not covered under this plan."
        elif category in plan["excluded_categories"]:
            covered = 0.0
            patient_pays = amount
            reason = f"{category.title()} charges are excluded under this plan."
            warnings.append(f"{category.title()} charges are excluded under the insurance plan.")
        elif category == "other":
            covered = amount * 0.5
            patient_pays = amount - covered
            reason = "Unrecognized bill category treated as 50% coverage."
            warnings.append(
                f"The item '{name}' is treated as 'other' category with 50% coverage."
            )
        else:
            covered = amount
            patient_pays = 0.0
            reason = "Covered under the plan."

        covered = round(covered, 2)
        patient_pays = round(patient_pays, 2)
        total_covered += covered
        total_patient_pays += patient_pays

        breakdown.append(
            {
                "item": name,
                "amount": amount,
                "covered": covered,
                "patient_pays": patient_pays,
                "reason": reason,
            }
        )

    deductible_applied = 0.0
    if plan["deductible"] > 0:
        deductible_applied = min(plan["deductible"], total_covered)
        total_covered -= deductible_applied
        total_patient_pays += deductible_applied
        warnings.append(
            f"A deductible of ₹{int(plan['deductible']):,} has been applied to this claim."
        )

    if plan["copay_percent"] > 0 and plan_key != "no_insurance":
        copay_amount = round(total_covered * plan["copay_percent"] / 100)
        total_covered -= copay_amount
        total_patient_pays += copay_amount
        warnings.append(
            f"An overall copay of {plan['copay_percent']}% has been applied to the covered amount."
        )

    sum_insured = plan["sum_insured"]
    if sum_insured and total_covered > sum_insured:
        excess = total_covered - sum_insured
        total_covered = sum_insured
        total_patient_pays += excess
        warnings.append(
            f"Bill exceeds sum insured of ₹{sum_insured:,}; coverage capped at the policy limit."
        )

    if sum_insured and total_bill >= sum_insured * 0.9:
        warnings.append(
            "The bill is approaching the policy sum insured limit."
        )

    coverage_percentage = round((total_covered / total_bill) * 100, 1) if total_bill > 0 else 0.0

    return {
        "total_bill": total_bill,
        "insurance_plan": plan["display_name"],
        "treatment_type": treatment_type,
        "breakdown": breakdown,
        "summary": {
            "total_covered": round(total_covered, 2),
            "total_patient_pays": round(total_patient_pays, 2),
            "deductible_applied": round(deductible_applied, 2),
            "coverage_percentage": coverage_percentage,
        },
        "warnings": warnings,
    }


def get_insurance_plans() -> List[Dict[str, Any]]:
    return [
        {
            "key": key,
            "display_name": plan["display_name"],
            "sum_insured": plan["sum_insured"],
            "room_rent_limit": plan["room_rent_limit"],
            "copay_percent": plan["copay_percent"],
            "medicine_copay": plan["medicine_copay"],
            "deductible": plan["deductible"],
            "excluded_categories": plan["excluded_categories"],
        }
        for key, plan in INSURANCE_PLANS.items()
    ]
