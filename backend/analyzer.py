"""AI-powered hospital bill analysis using Google Gemini Vision (free tier)."""

import json
import base64
import os
import time
import uuid
from datetime import datetime
from .models import BillAnalysis, LineItem, SeverityLevel

# Reference rates (CGHS 2024 / common Bangalore hospital benchmarks)
REFERENCE_RATES = {
    "categories": {
        "room_charges": {
            "general_ward": {"min": 500, "max": 2000, "unit": "per_day"},
            "semi_private": {"min": 2000, "max": 5000, "unit": "per_day"},
            "private_room": {"min": 4000, "max": 10000, "unit": "per_day"},
            "icu": {"min": 8000, "max": 25000, "unit": "per_day"},
        },
        "common_medications": {
            "paracetamol_500mg": {"max_mrp": 2.5, "note": "per tablet"},
            "ceftriaxone_1g_iv": {"max_mrp": 85, "note": "per vial"},
            "pantoprazole_40mg_iv": {"max_mrp": 75, "note": "per vial"},
            "ondansetron_4mg_iv": {"max_mrp": 35, "note": "per vial"},
            "normal_saline_500ml": {"max_mrp": 30, "note": "per bottle"},
            "dextrose_5pct_500ml": {"max_mrp": 35, "note": "per bottle"},
        },
        "common_consumables": {
            "surgical_gloves_pair": {"max_mrp": 15},
            "iv_cannula": {"max_mrp": 45},
            "syringe_5ml": {"max_mrp": 8},
            "cotton_roll": {"max_mrp": 25},
            "surgical_mask": {"max_mrp": 10},
        },
        "common_procedures_cghs": {
            "complete_blood_count": {"cghs_rate": 150},
            "blood_sugar_fasting": {"cghs_rate": 50},
            "liver_function_test": {"cghs_rate": 250},
            "kidney_function_test": {"cghs_rate": 250},
            "chest_xray": {"cghs_rate": 200},
            "usg_abdomen": {"cghs_rate": 600},
            "ct_scan_plain": {"cghs_rate": 3000},
            "ct_scan_contrast": {"cghs_rate": 5000},
            "mri_plain": {"cghs_rate": 5000},
            "mri_contrast": {"cghs_rate": 8000},
            "ecg": {"cghs_rate": 150},
            "echo_2d": {"cghs_rate": 1000},
        }
    }
}

ANALYSIS_PROMPT = """You are MedBill Check AI, an expert hospital bill auditor for Indian hospitals.
Analyze this hospital bill image and extract every line item with detailed information.

Your task:
1. EXTRACT every line item from the bill — medications, consumables, procedures, room charges, doctor fees, lab tests, misc charges.
2. For each item, provide: description, category, quantity, unit price, billed amount.
3. FLAG potential overcharges by comparing against these reference benchmarks:

REFERENCE RATES (CGHS 2024 / Common Bangalore benchmarks):
{reference_rates}

4. Look for these COMMON OVERCHARGE PATTERNS:
   - Double billing: same item appearing twice
   - Phantom charges: items unlikely to be used for the stated diagnosis
   - Unbundling: items that should be included in a package/procedure being charged separately
   - MRP markup: medications charged above MRP (illegal in India)
   - Inflated consumables: basic items like gloves, syringes charged 5-10x market rate
   - Room charge padding: billing for a higher category room than occupied
   - Surgeon/anesthetist fee inflation: fees significantly above CGHS benchmarks

5. Provide a confidence score (0-1) for each flag based on how certain you are.

IMPORTANT RULES:
- Do NOT invent data. If you can't read a line item clearly, say so.
- If you can't determine the reference rate, say "reference rate unavailable" — don't guess.
- Be conservative: flag only clear overcharges, not borderline cases.
- Note limitations: OCR quality, items you couldn't read, categories you don't have references for.

Respond in this exact JSON format (no markdown, no code fences, just raw JSON):
{{
    "hospital_name": "string or null",
    "patient_name": "string or null (redact last name to initial)",
    "bill_date": "string or null",
    "bill_number": "string or null",
    "total_billed": number,
    "line_items": [
        {{
            "sl_no": number or null,
            "description": "string",
            "category": "medication|consumable|procedure|room|doctor_fee|lab|misc",
            "quantity": number or null,
            "unit_price": number or null,
            "billed_amount": number,
            "reference_amount": number or null,
            "reference_source": "string or null",
            "flag": "overcharge|double_billing|phantom|unbundled|null",
            "severity": "high|medium|low|info|null",
            "explanation": "string or null",
            "potential_savings": number
        }}
    ],
    "summary": "2-3 sentence summary of findings",
    "recommendations": ["list of specific actions the patient should take"],
    "confidence_score": number between 0 and 1,
    "limitations": ["list of things you couldn't verify or read clearly"]
}}"""


def _demo_analysis(bill_id: str) -> BillAnalysis:
    """Return a realistic demo analysis when no API key is available."""
    demo_items = [
        LineItem(sl_no=1, description="Room Charges - Private Room", category="room",
                 quantity=3, unit_price=8000, billed_amount=24000,
                 reference_amount=6000, reference_source="CGHS 2024 General Ward Rate",
                 flag="overcharge", severity=SeverityLevel.HIGH,
                 explanation="Patient was admitted in General Ward but billed at Private Room rate (Rs 8,000/day vs Rs 2,000/day for general ward).",
                 potential_savings=18000),
        LineItem(sl_no=2, description="Paracetamol 500mg Tablets", category="medication",
                 quantity=20, unit_price=15, billed_amount=300,
                 reference_amount=50, reference_source="MRP Schedule",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="Paracetamol 500mg MRP is Rs 2.50/tablet. Charged Rs 15/tablet — 6x markup above MRP, which is illegal under DPCO.",
                 potential_savings=250),
        LineItem(sl_no=3, description="Ceftriaxone 1g IV", category="medication",
                 quantity=6, unit_price=250, billed_amount=1500,
                 reference_amount=510, reference_source="MRP Schedule",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="Ceftriaxone 1g IV MRP is Rs 85/vial. Charged Rs 250/vial — nearly 3x MRP.",
                 potential_savings=990),
        LineItem(sl_no=4, description="Normal Saline 500ml", category="medication",
                 quantity=8, unit_price=120, billed_amount=960,
                 reference_amount=240, reference_source="MRP Schedule",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="Normal Saline MRP is Rs 30/bottle. Charged Rs 120 — 4x markup.",
                 potential_savings=720),
        LineItem(sl_no=5, description="Surgical Gloves (pair)", category="consumable",
                 quantity=30, unit_price=50, billed_amount=1500,
                 reference_amount=450, reference_source="Market rate",
                 flag="overcharge", severity=SeverityLevel.LOW,
                 explanation="Surgical gloves max MRP Rs 15/pair. Charged Rs 50/pair.",
                 potential_savings=1050),
        LineItem(sl_no=6, description="IV Cannula", category="consumable",
                 quantity=4, unit_price=150, billed_amount=600,
                 reference_amount=180, reference_source="Market rate",
                 flag="overcharge", severity=SeverityLevel.LOW,
                 explanation="IV cannula MRP is Rs 45. Charged Rs 150 — over 3x.",
                 potential_savings=420),
        LineItem(sl_no=7, description="Syringe 5ml", category="consumable",
                 quantity=20, unit_price=30, billed_amount=600,
                 reference_amount=160, reference_source="Market rate",
                 flag="overcharge", severity=SeverityLevel.LOW,
                 explanation="Syringe 5ml MRP is Rs 8. Charged Rs 30.",
                 potential_savings=440),
        LineItem(sl_no=8, description="Complete Blood Count (CBC)", category="lab",
                 quantity=2, unit_price=500, billed_amount=1000,
                 reference_amount=300, reference_source="CGHS 2024",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="CBC CGHS rate is Rs 150. Charged Rs 500 — over 3x the benchmark.",
                 potential_savings=700),
        LineItem(sl_no=9, description="Liver Function Test", category="lab",
                 quantity=1, unit_price=800, billed_amount=800,
                 reference_amount=250, reference_source="CGHS 2024",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="LFT CGHS rate is Rs 250. Charged Rs 800.",
                 potential_savings=550),
        LineItem(sl_no=10, description="Chest X-Ray", category="lab",
                 quantity=1, unit_price=600, billed_amount=600,
                 reference_amount=200, reference_source="CGHS 2024",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="Chest X-Ray CGHS rate is Rs 200. Charged Rs 600.",
                 potential_savings=400),
        LineItem(sl_no=11, description="ECG", category="lab",
                 quantity=1, unit_price=500, billed_amount=500,
                 reference_amount=150, reference_source="CGHS 2024",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="ECG CGHS rate is Rs 150. Charged Rs 500.",
                 potential_savings=350),
        LineItem(sl_no=12, description="USG Abdomen", category="lab",
                 quantity=1, unit_price=1800, billed_amount=1800,
                 reference_amount=600, reference_source="CGHS 2024",
                 flag="overcharge", severity=SeverityLevel.MEDIUM,
                 explanation="USG Abdomen CGHS rate is Rs 600. Charged Rs 1,800 — 3x benchmark.",
                 potential_savings=1200),
        LineItem(sl_no=13, description="Doctor Visit Charges", category="doctor_fee",
                 quantity=6, unit_price=1000, billed_amount=6000),
        LineItem(sl_no=14, description="Nursing Charges", category="misc",
                 quantity=3, unit_price=2000, billed_amount=6000),
        LineItem(sl_no=15, description="Surgical Mask", category="consumable",
                 quantity=50, unit_price=25, billed_amount=1250,
                 reference_amount=500, reference_source="Market rate",
                 flag="overcharge", severity=SeverityLevel.LOW,
                 explanation="Surgical mask MRP is Rs 10. Charged Rs 25.",
                 potential_savings=750),
        LineItem(sl_no=16, description="Cotton Roll", category="consumable",
                 quantity=10, unit_price=80, billed_amount=800,
                 reference_amount=250, reference_source="Market rate",
                 flag="overcharge", severity=SeverityLevel.LOW,
                 explanation="Cotton roll MRP is Rs 25. Charged Rs 80.",
                 potential_savings=550),
        LineItem(sl_no=17, description="Complete Blood Count (CBC)", category="lab",
                 quantity=2, unit_price=500, billed_amount=1000,
                 reference_amount=0, reference_source="Duplicate entry",
                 flag="double_billing", severity=SeverityLevel.HIGH,
                 explanation="CBC appears twice on the bill (Sl 8 and Sl 17) with identical charges. This is likely a duplicate billing error.",
                 potential_savings=1000),
    ]

    flagged = [i for i in demo_items if i.flag]
    total_savings = sum(i.potential_savings for i in flagged)
    total_billed = sum(i.billed_amount for i in demo_items)
    total_expected = sum(i.reference_amount or i.billed_amount for i in demo_items)

    return BillAnalysis(
        bill_id=bill_id,
        analyzed_at=datetime.now(),
        hospital_name="Bangalore City Hospital",
        patient_name="Suhas G.",
        bill_date="15-Mar-2024",
        bill_number="BCH-2024-7892",
        total_billed=total_billed,
        total_expected=total_expected,
        total_potential_savings=total_savings,
        line_items=demo_items,
        flagged_items=flagged,
        summary="This bill shows significant overcharging across multiple categories. The most critical issue is room charges billed at Private Room rates (Rs 8,000/day) when the patient was admitted in a General Ward (Rs 2,000/day benchmark). Multiple medications and consumables are charged well above MRP, which is illegal under India's Drug Price Control Order. A duplicate CBC charge was also detected.",
        recommendations=[
            "Dispute room charges immediately — request correction from Private Room to General Ward rate, saving Rs 18,000",
            "Challenge all medication charges above MRP with the hospital billing department, citing DPCO regulations",
            "Request removal of duplicate CBC charge (Sl 17) — this is a clear billing error worth Rs 1,000",
            "File a complaint with the hospital's grievance cell if charges are not corrected within 7 days",
            "Consider filing a complaint with the State Consumer Disputes Redressal Commission if the hospital does not respond",
        ],
        confidence_score=0.82,
        limitations=[
            "Room category (General vs Private) based on patient's note — could not verify from bill image alone",
            "MRP references are based on standard published rates and may vary by brand",
            "Doctor visit charges benchmarks vary widely and were not flagged without stronger evidence",
            "DEMO MODE: This analysis uses pre-built sample data. Set GEMINI_API_KEY for real AI-powered analysis.",
        ],
    )


async def analyze_bill(image_base64: str, notes: str | None = None) -> BillAnalysis:
    """Analyze a hospital bill image using Google Gemini Vision."""

    start_time = time.time()
    bill_id = str(uuid.uuid4())[:8]

    # Check if API key is available; fall back to demo mode if not
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        import asyncio
        await asyncio.sleep(2)  # Simulate processing time
        return _demo_analysis(bill_id)

    from google import genai

    client = genai.Client(api_key=api_key)

    # Detect MIME type from base64 header
    mime_type = "image/jpeg"
    if image_base64.startswith("/9j/"):
        mime_type = "image/jpeg"
    elif image_base64.startswith("iVBOR"):
        mime_type = "image/png"
    elif image_base64.startswith("UklGR"):
        mime_type = "image/webp"
    elif image_base64.startswith("JVBER"):
        mime_type = "application/pdf"

    # Build the prompt with reference rates
    prompt = ANALYSIS_PROMPT.format(
        reference_rates=json.dumps(REFERENCE_RATES, indent=2)
    )

    if notes:
        prompt += f"\n\nAdditional context from the patient: {notes}"

    # Call Gemini Vision
    response = client.models.generate_content(
                model="gemini-3.8-flash",
        contents=[
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_base64,
                        }
                    },
                    {
                        "text": prompt,
                    }
                ]
            }
        ],
    )

    # Parse the response
    response_text = response.text

    # Extract JSON from the response (handle markdown code blocks)
    if "```json" in response_text:
        response_text = response_text.split("```json")[1].split("```")[0]
    elif "```" in response_text:
        response_text = response_text.split("```")[1].split("```")[0]

    data = json.loads(response_text.strip())

    # Build LineItem objects
    line_items = []
    flagged_items = []
    total_savings = 0.0

    for item_data in data.get("line_items", []):
        item = LineItem(
            sl_no=item_data.get("sl_no"),
            description=item_data.get("description", "Unknown"),
            category=item_data.get("category", "misc"),
            quantity=item_data.get("quantity"),
            unit_price=item_data.get("unit_price"),
            billed_amount=item_data.get("billed_amount", 0),
            reference_amount=item_data.get("reference_amount"),
            reference_source=item_data.get("reference_source"),
            flag=item_data.get("flag"),
            severity=item_data.get("severity"),
            explanation=item_data.get("explanation"),
            potential_savings=item_data.get("potential_savings", 0),
        )
        line_items.append(item)
        if item.flag:
            flagged_items.append(item)
            total_savings += item.potential_savings

    # Calculate total expected
    total_expected = None
    if any(item.reference_amount for item in line_items):
        total_expected = sum(
            item.reference_amount or item.billed_amount
            for item in line_items
        )

    analysis = BillAnalysis(
        bill_id=bill_id,
        analyzed_at=datetime.now(),
        hospital_name=data.get("hospital_name"),
        patient_name=data.get("patient_name"),
        bill_date=data.get("bill_date"),
        bill_number=data.get("bill_number"),
        total_billed=data.get("total_billed", 0),
        total_expected=total_expected,
        total_potential_savings=total_savings,
        line_items=line_items,
        flagged_items=flagged_items,
        summary=data.get("summary", "Analysis complete."),
        recommendations=data.get("recommendations", []),
        confidence_score=data.get("confidence_score", 0.5),
        limitations=data.get("limitations", []),
    )

    return analysis
