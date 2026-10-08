"""FastAPI backend for MedBill Check AI."""

import base64
import time
import json
import os
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .analyzer import analyze_bill
from .report_generator import generate_report_pdf
from .models import AuditResponse, BillAnalysis

app = FastAPI(title="MedBill Check AI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory store for analyses (SQLite in production)
analyses_store: dict[str, BillAnalysis] = {}

# Serve frontend
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


@app.post("/api/analyze")
async def analyze_bill_endpoint(
    file: UploadFile = File(...),
    notes: str = Form(default=""),
):
    """Upload a hospital bill image and get an AI-powered audit."""

    start_time = time.time()

    # Validate file type
    allowed_types = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
    if file.content_type not in allowed_types:
        raise HTTPException(400, f"File type {file.content_type} not supported. Use JPEG, PNG, WebP, or PDF.")

    # Read and encode
    contents = await file.read()
    if len(contents) > 10 * 1024 * 1024:  # 10MB limit
        raise HTTPException(400, "File too large. Maximum size is 10MB.")

    image_base64 = base64.b64encode(contents).decode("utf-8")

    try:
        analysis = await analyze_bill(image_base64, notes or None)
        analyses_store[analysis.bill_id] = analysis

        processing_time = time.time() - start_time

        return AuditResponse(
            success=True,
            analysis=analysis,
            processing_time_seconds=round(processing_time, 2),
        )
    except json.JSONDecodeError as e:
        return AuditResponse(
            success=False,
            error=f"Could not parse the bill. The image may be unclear or not a hospital bill. Details: {str(e)}",
        )
    except Exception as e:
        return AuditResponse(
            success=False,
            error=f"Analysis failed: {str(e)}",
        )


@app.get("/api/report/{bill_id}")
async def get_report_pdf(bill_id: str):
    """Download the PDF audit report for a completed analysis."""

    if bill_id not in analyses_store:
        raise HTTPException(404, "Analysis not found. Please run the audit first.")

    analysis = analyses_store[bill_id]
    pdf_bytes = generate_report_pdf(analysis)

    filename = f"MedBill_Audit_{analysis.hospital_name or 'Report'}_{analysis.bill_id}.pdf"
    filename = filename.replace(" ", "_")

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/analysis/{bill_id}")
async def get_analysis(bill_id: str):
    """Retrieve a previous analysis result."""

    if bill_id not in analyses_store:
        raise HTTPException(404, "Analysis not found.")

    return AuditResponse(success=True, analysis=analyses_store[bill_id])


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}


# Serve frontend (catch-all must be last)
if FRONTEND_DIR.exists():


    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = FRONTEND_DIR / full_path
        if file_path.exists() and file_path.is_file():
            return FileResponse(file_path)
        return FileResponse(FRONTEND_DIR / "index.html")
