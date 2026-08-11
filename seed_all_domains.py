"""
Seed script — inserts realistic sample data into every domain table so all
pages in the UniDocVerse dashboard display real content immediately.

Run from inside unidocverse-service/:
    python seed_all_domains.py
"""
import uuid
from datetime import date, timedelta
import psycopg2

DB = dict(host="localhost", port=54321, dbname="unidocverse_db",
          user="unidocverse", password="unidocverse")

def run():
    conn = psycopg2.connect(**DB)
    conn.autocommit = False
    cur = conn.cursor()

    # ── helpers ────────────────────────────────────────────────────────────────
    def uid(): return str(uuid.uuid4())
    def days(n): return (date.today() + timedelta(days=n)).isoformat()

    print("Seeding all domain tables …")

    # ── 1. FAKE DOCUMENT ROWS (needed as FK for most tables) ──────────────────
    # We insert minimal rows into `documents` so FK constraints hold.
    doc_types = [
        ("medical_chart",        "Patient_Chart_John_Smith.pdf"),
        ("intake_form",          "Intake_Form_Jane_Doe.pdf"),
        ("lab_report",           "Lab_Report_Bob_Lee.pdf"),
        ("invoice",              "Invoice_Acme_Corp_2026.pdf"),
        ("bank_statement",       "Bank_Statement_June_2026.pdf"),
        ("tax_form",             "Tax_Return_2025.pdf"),
        ("appraisal",            "Appraisal_123_Main_St.pdf"),
        ("title_deed",           "Title_Deed_456_Oak_Ave.pdf"),
        ("closing_disclosure",   "Closing_Disclosure_789_Elm.pdf"),
        ("resume",               "Resume_Alice_Johnson.pdf"),
        ("handbook",             "Employee_Handbook_v3.pdf"),
        ("performance_review",   "Performance_Review_Q2.pdf"),
        ("purchase_order",       "PO_Vendor_Delta_2026.pdf"),
        ("bill_of_lading",       "BOL_Shipment_SHP001.pdf"),
        ("customs_manifest",     "Customs_Manifest_CM001.pdf"),
        ("contract",             "Master_Services_Agreement_Acme.pdf"),
        ("nda",                  "NDA_Beta_Corp.pdf"),
        ("lease_agreement",      "Lease_Agreement_Office_Suite.pdf"),
        ("regulatory_filing",    "SEC_Filing_Annual_Report.pdf"),
    ]
    doc_ids = {}
    for dt, fname in doc_types:
        did = uid()
        doc_ids[dt] = did
        cur.execute("""
            INSERT INTO documents (id, filename, file_path, doc_type, status, raw_text, file_size, created_at, updated_at)
            VALUES (%s, %s, %s, %s, 'completed', %s, 102400, NOW(), NOW())
            ON CONFLICT DO NOTHING
        """, (did, fname, f'/seed/{fname}', dt, f'Sample document text for {fname}'))

    # ── 2. HEALTHCARE ──────────────────────────────────────────────────────────
    patients = [
        ("John Smith",   date(1972, 3, 15), 88, True,  True,  True,  "medical_chart"),
        ("Jane Doe",     date(1985, 7, 22), 76, True,  True,  False, "intake_form"),
        ("Bob Lee",      date(1968, 11, 5), 92, False, True,  True,  "lab_report"),
        ("Maria Torres", date(1990, 1, 30), 65, True,  False, True,  "medical_chart"),
        ("David Kim",    date(1955, 9, 12), 81, True,  True,  True,  "intake_form"),
    ]
    pc_ids = {}
    for name, dob, score, consent, insured, history, dtype in patients:
        pid = uid()
        pc_ids[name] = pid
        cur.execute("""
            INSERT INTO patient_charts
              (id, document_id, patient_name, patient_dob, chart_score, consent_signed, insurance_verified, history_filed)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (pid, doc_ids[dtype], name, dob, score, consent, insured, history))

    biomarkers = [
        ("medical_chart", "Total Cholesterol", 210, "mg/dL", "125-200", True,  "Dr. Patel"),
        ("medical_chart", "Glucose",           95,  "mg/dL", "70-100",  False, "Dr. Patel"),
        ("intake_form",   "Total Cholesterol", 185, "mg/dL", "125-200", False, "Dr. Nguyen"),
        ("intake_form",   "Triglycerides",     320, "mg/dL", "< 150",   True,  "Dr. Nguyen"),
        ("lab_report",    "Glucose",           140, "mg/dL", "70-100",  True,  "Dr. Chen"),
        ("medical_chart", "Triglycerides",     180, "mg/dL", "< 150",   True,  "Dr. Kim"),
    ]
    for dtype, bname, val, unit, ref, abnormal, phys in biomarkers:
        cur.execute("""
            INSERT INTO patient_biomarkers
              (document_id, biomarker_name, biomarker_value, unit, reference_range, is_abnormal, physician)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (doc_ids[dtype], bname, val, unit, ref, abnormal, phys))

    # ── 3. LEGAL ───────────────────────────────────────────────────────────────
    contracts = [
        ("Master Services Agreement — Acme Corp",  "Acme Corp",          "30 Days",  date(2027, 6, 30),  "Low",    "Active",  "contract"),
        ("Software License — Beta Technologies",   "Beta Technologies",  "60 Days",  date(2026, 12, 31), "Medium", "Active",  "contract"),
        ("Consulting Agreement — Gamma Solutions", "Gamma Solutions",    "45 Days",  date(2027, 3, 15),  "High",   "Review",  "contract"),
        ("NDA — Beta Corp",                        "Beta Corp",          "30 Days",  date(2027, 9, 1),   "Low",    "Active",  "nda"),
        ("NDA — CloudTech Inc",                    "CloudTech Inc",      "30 Days",  date(2026, 10, 31), "Low",    "Active",  "nda"),
        ("Office Suite Lease — Harbor Realty",     "Harbor Realty Group","90 Days",  date(2029, 1, 1),   "Medium", "Active",  "lease_agreement"),
        ("Warehouse Lease — Metro Logistics",      "Metro Logistics LLC","60 Days",  date(2028, 6, 30),  "Low",    "Active",  "lease_agreement"),
        ("SEC Annual Report Filing",               "SEC / EDGAR",        "N/A",      date(2027, 4, 15),  "Low",    "Filed",   "regulatory_filing"),
        ("HIPAA Privacy Notice — DoH",             "Dept of Health",     "N/A",      date(2027, 1, 1),   "Medium", "Active",  "regulatory_filing"),
    ]
    for cname, cp, notice, expiry, risk, status, dtype in contracts:
        cur.execute("""
            INSERT INTO legal_contracts
              (id, document_id, contract_name, counterparty, notice_period, expiry_date, risk_rating, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (uid(), doc_ids[dtype], cname, cp, notice, expiry, risk, status))

    clauses = [
        ("Indemnification",   "Standard Indemnification Clause",       "Each party shall indemnify the other...", False, "Delaware"),
        ("Confidentiality",   "Mutual Non-Disclosure",                 "Both parties agree to maintain strict confidentiality...", False, "New York"),
        ("Termination",       "30-Day Termination for Convenience",    "Either party may terminate upon 30 days written notice...", False, "Delaware"),
        ("Governing Law",     "Jurisdiction — California",             "This agreement shall be governed by the laws of California.", True,  "California"),
        ("Liability Cap",     "Liability Limitation Clause",           "In no event shall either party's liability exceed...", False, "Delaware"),
    ]
    for ctype, cname, ctext, dev, gov in clauses:
        cur.execute("""
            INSERT INTO legal_clauses (document_id, clause_type, clause_name, clause_text, is_deviation, governing_law)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (doc_ids["contract"], ctype, cname, ctext, dev, gov))

    # ── 4. FINANCE ─────────────────────────────────────────────────────────────
    matches = [
        ("INV-2026-001", "Acme Corp",          12500.00, 12500.00, 12480.00, "matched",   True),
        ("INV-2026-002", "Acme Corp",           8200.00,  8200.00,  8200.00, "matched",   True),
        ("INV-2026-003", "Beta Technologies",  45000.00, 45000.00, 44800.00, "discrepancy",False),
        ("INV-2026-004", "Gamma Solutions",    22000.00, 22000.00, 22000.00, "matched",   True),
        ("INV-2026-005", "Delta Logistics",     5400.00,  5000.00,  5400.00, "discrepancy",False),
        ("INV-2026-006", "Acme Corp",           3100.00,  3100.00,  3100.00, "matched",   True),
        ("INV-2026-007", "Beta Technologies",  18000.00, 18000.00, 18000.00, "matched",   True),
    ]
    for inv_id, vendor, inv_val, po_val, rec_val, status, approved in matches:
        cur.execute("""
            INSERT INTO finance_matches
              (id, document_id, invoice_id, vendor_name, invoice_val, po_val, receipt_val, match_status, is_approved)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (uid(), doc_ids["invoice"], inv_id, vendor, inv_val, po_val, rec_val, status, approved))

    anomalies = [
        ("duplicate_invoice", "Duplicate Invoice Detected",           "INV-2026-003", 45000.00),
        ("amount_mismatch",   "PO vs Receipt Amount Mismatch",        "INV-2026-005",   400.00),
        ("missing_po",        "Invoice Without Matching PO",          "INV-2026-008",  7500.00),
    ]
    for atype, title, ref, amount in anomalies:
        cur.execute("""
            INSERT INTO finance_anomalies (document_id, anomaly_type, title, ref_code, amount)
            VALUES (%s, %s, %s, %s, %s)
        """, (doc_ids["bank_statement"], atype, title, ref, amount))

    # ── 5. REAL ESTATE ─────────────────────────────────────────────────────────
    props = [
        ("123 Main St, San Francisco CA", "Alice Johnson", 1250000.00, 75, True,  True,  True,  "appraisal"),
        ("456 Oak Ave, Los Angeles CA",   "Bob Martinez",   875000.00, 45, True,  False, True,  "title_deed"),
        ("789 Elm Blvd, San Diego CA",    "Carol White",   1100000.00, 90, True,  True,  True,  "closing_disclosure"),
        ("321 Pine Rd, Sacramento CA",    "Dan Brown",      650000.00, 20, False, True,  False, "appraisal"),
    ]
    prop_ids = {}
    for addr, buyer, price, progress, appraisal, title, escrow, dtype in props:
        pid = uid()
        prop_ids[addr] = pid
        cur.execute("""
            INSERT INTO real_estate_properties
              (id, document_id, address, buyer_name, price, progress, has_appraisal, has_clear_title, is_escrow_funded)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (pid, doc_ids[dtype], addr, buyer, price, progress, appraisal, title, escrow))

    milestones_data = [
        ("123 Main St, San Francisco CA", [
            ("Appraisal Ordered",         -30, True),
            ("Inspection Completed",      -20, True),
            ("Title Search",              -15, True),
            ("Loan Underwriting",          -5, False),
            ("Final Walkthrough",           3, False),
            ("Closing Date",               7, False),
        ]),
        ("456 Oak Ave, Los Angeles CA", [
            ("Appraisal Ordered",         -14, True),
            ("Inspection Completed",       -7, True),
            ("Title Search",               -2, False),
            ("Loan Underwriting",          14, False),
        ]),
        ("789 Elm Blvd, San Diego CA", [
            ("Appraisal Ordered",         -45, True),
            ("Inspection Completed",      -35, True),
            ("Title Search",              -30, True),
            ("Loan Underwriting",         -20, True),
            ("Final Walkthrough",         -10, True),
            ("Closing Date",               -3, True),
        ]),
    ]
    for addr, mstones in milestones_data:
        pid = prop_ids.get(addr)
        if not pid:
            continue
        for mname, day_offset, done in mstones:
            cur.execute("""
                INSERT INTO real_estate_milestones (property_id, name, due_date, is_completed)
                VALUES (%s, %s, %s, %s)
            """, (pid, mname, days(day_offset), done))

    # ── 6. HR ──────────────────────────────────────────────────────────────────
    candidates = [
        ("Alice Johnson",  "Senior Software Engineer",   8, 92, ["Python","React","AWS"],     "Strong hire — exceptional portfolio"),
        ("Carlos Rivera",  "DevOps Engineer",             5, 85, ["Kubernetes","Terraform","CI/CD"], "Good fit — solid systems background"),
        ("Emily Zhang",    "Product Manager",             7, 88, ["Agile","Roadmapping","SQL"], "Recommended — excellent communication"),
        ("Frank Williams", "Data Scientist",              4, 78, ["Python","ML","Pandas"],      "Promising — needs mentorship on scale"),
        ("Grace Lee",      "UX Designer",                 6, 90, ["Figma","User Research"],     "Top candidate — outstanding portfolio"),
    ]
    cand_ids = {}
    for name, role, exp, score, skills, feedback in candidates:
        cid = uid()
        cand_ids[name] = cid
        cur.execute("""
            INSERT INTO hr_candidates
              (id, document_id, candidate_name, role, experience_years, match_score, skills, feedback)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (cid, doc_ids["resume"], name, role, exp, score, skills, feedback))

    onboarding = [
        ("Alice Johnson",  "Complete I-9 Employment Verification",     -2, True),
        ("Alice Johnson",  "IT Setup — Laptop & Accounts",              0, True),
        ("Alice Johnson",  "Review Employee Handbook",                   3, False),
        ("Alice Johnson",  "Meet Engineering Team — Introductions",      5, False),
        ("Carlos Rivera",  "Complete Background Check",                 -5, True),
        ("Carlos Rivera",  "Sign Confidentiality Agreement",            -3, True),
        ("Carlos Rivera",  "DevOps Environment Setup",                   1, False),
        ("Emily Zhang",    "Complete I-9 Employment Verification",      -1, True),
        ("Emily Zhang",    "Product Onboarding Session",                 4, False),
        ("Frank Williams", "Data Access & Security Training",            2, False),
        ("Grace Lee",      "Design Tools License Setup",                -1, True),
        ("Grace Lee",      "Portfolio Review with Design Lead",          6, False),
    ]
    for emp, title, day_offset, done in onboarding:
        cur.execute("""
            INSERT INTO hr_onboarding_tasks (document_id, title, employee_name, due_date, is_done)
            VALUES (%s, %s, %s, %s, %s)
        """, (doc_ids["handbook"], title, emp, days(day_offset), done))

    # ── 7. SUPPLY CHAIN ────────────────────────────────────────────────────────
    shipments = [
        ("BOL-2026-001", "FastFreight LLC",     "Shanghai, CN",       "Los Angeles, CA",  "Cleared",  True),
        ("BOL-2026-002", "GlobalCargo Inc",     "Rotterdam, NL",      "New York, NY",     "Pending",  False),
        ("BOL-2026-003", "FastFreight LLC",     "Tokyo, JP",          "Seattle, WA",      "Cleared",  True),
        ("BOL-2026-004", "OceanBridge Co",      "Mumbai, IN",         "Houston, TX",      "Hold",     False),
        ("BOL-2026-005", "AirExpress Corp",     "Frankfurt, DE",      "Chicago, IL",      "Cleared",  True),
        ("BOL-2026-006", "GlobalCargo Inc",     "Singapore, SG",      "Miami, FL",        "Cleared",  True),
        ("BOL-2026-007", "OceanBridge Co",      "Cape Town, ZA",      "Baltimore, MD",    "Pending",  False),
    ]
    for bol, carrier, origin, dest, customs, reconciled in shipments:
        cur.execute("""
            INSERT INTO supply_chain_shipments
              (id, document_id, bol_number, carrier_name, origin, destination, customs_status, is_reconciled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (uid(), doc_ids["bill_of_lading"], bol, carrier, origin, dest, customs, reconciled))

    suppliers = [
        ("FastFreight LLC",     "Freight Carrier",   date(2027, 3, 31), "pass"),
        ("GlobalCargo Inc",     "Ocean Freight",     date(2026, 12, 31),"pass"),
        ("OceanBridge Co",      "Bulk Shipping",     date(2027, 6, 30), "review"),
        ("AirExpress Corp",     "Air Freight",       date(2027, 1, 31), "pass"),
        ("Delta Parts Inc",     "Parts Supplier",    date(2026, 9, 30), "pass"),
        ("Sigma Electronics",   "Electronics OEM",   date(2027, 9, 30), "pass"),
        ("Apex Customs Broker", "Customs Broker",    date(2027, 12, 31),"pass"),
    ]
    for name, stype, expiry, sanctions in suppliers:
        cur.execute("""
            INSERT INTO supply_chain_suppliers (id, document_id, name, type, expiry_date, sanctions_status)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (uid(), doc_ids["customs_manifest"], name, stype, expiry, sanctions))

    conn.commit()
    cur.close()
    conn.close()
    print("✅  Seed complete — all domain tables populated.")
    print("    Domains covered: Legal, Healthcare, Finance, Real Estate, HR, Supply Chain")

if __name__ == "__main__":
    run()
