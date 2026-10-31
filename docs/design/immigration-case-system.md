# Immigration case system — design

Status: **draft for review** · Author: Vijay Kumar Bomma · Oct 2026

## 1. Goal

Run an immigration matter the way a firm does, end to end: intake → engagement → strategy →
evidence → preparation → review → filing → USCIS tracking → RFE → decision → what comes next.
Documents are inputs that move the matter forward; people approve every step with legal weight.

Three questions the system must always answer, per person:

1. **In status until when?** — dated status periods, not a single expiry field.
2. **What is due next, and who owns it?** — stored deadlines with an owner, a backup and escalation.
3. **Do we have what we need to win?** — evidence per filing, with state and quality checks.

Non-goals for this phase: billing/trust accounting (export only), e-filing to USCIS (no public API),
consular processing beyond tracking dates.

## 2. What exists today (kept)

| Piece | Where | Keep / change |
|---|---|---|
| Cases, events, tasks, checklist | `imm_cases`, `imm_case_events`, `imm_case_tasks`, `imm_case_checklist`; `suite.py` | Keep; `imm_cases` becomes the **matter**; receipt/dates move to filings |
| Case types, evidence lists, filing windows | `immigration_rules.yaml` | Becomes the **template** file (stages + triggers added) |
| Task templates | `task_templates.yaml` | Keep; attached to template stages |
| Leads → case, intake questionnaire, e-signature | `suite.py` | Keep as is |
| Official form pre-fill (I-90, I-129, I-130, I-140, I-485, I-539, I-765) | `immigration/forms/` | Keep; read from the person + matter model instead of free text |
| Visa tracker records | `client_immigration`, `immigration_tracker` | Replaced by `imm_status_periods` |
| New person → approval → client | `immigration_workflow_service.py` | Keep; also proposes the matter |

### Gaps this design closes

- A case names its beneficiary as **text**; nothing links it to a client or to the petitioner/dependents.
- **One receipt number per case** — a green-card matter has I-140, I-485, I-765, I-131, each with its own.
- Status is a **flat list** (`intake … approved`); no stages per case type, no rule for what moves it.
- Deadlines are **computed on the fly** — no owner, no reminder history, no escalation, nothing to audit.
- Checklist matching is "the name appears in the text"; no evidence states, no quality checks.
- USCIS notices are classified but **don't change the case**.
- No conflict check or engagement gate before work starts.

## 3. Data model

All tables carry `tenant_id` with FORCE row-level security (as V73). New migration: **V74**.
Existing applied migrations are not edited.

```
imm_people            one row per human (client, beneficiary, petitioner, dependent)
  id, tenant_id, client_id → clients (nullable until approved), family_name, given_name, middle_name,
  dob, country_of_birth, citizenship, a_number, uscis_account, passport_no, passport_expiry,
  email, phone, created_from_document_id, created_at

imm_orgs              employers / sponsors
  id, tenant_id, name, fein_last4, address, hr_contact_name, hr_contact_email

imm_matter_parties    who plays which role in a matter (a person can have several)
  matter_id → imm_cases, person_id | org_id, role: beneficiary|petitioner|dependent|sponsor|interpreter

imm_filings           one USCIS form within a matter
  id, matter_id, tenant_id, form (I-140, I-485 …), receipt_number, service_center,
  stage, filed_on, receipt_on, biometrics_on, interview_on, rfe_due_on, decided_on,
  decision: approved|denied|withdrawn|null, filed_package_document_id, created_at

imm_status_periods    "in status until when"
  id, tenant_id, person_id, kind: H-1B|H-4|F-1|LPR|EAD|AP|I-94|passport|…,
  valid_from, valid_to, source_document_id, source_filing_id, superseded_by

imm_evidence          checklist line, per filing
  id, filing_id, tenant_id, item, required, state: requested|received|reviewed|rejected|waived,
  document_id, requested_from: client|employer|firm, requested_on, reviewed_by, reviewed_on, note

imm_deadlines         the docket
  id, tenant_id, matter_id, filing_id, kind (rfe_response, biometrics, filing_window_opens,
  appeal, status_expiry, …), due_on, owner, backup_owner, source: rule|notice|manual,
  source_ref, state: open|done|missed|cancelled, done_by, done_on, reminders_sent jsonb

imm_rfe_items         an RFE split into individual requests
  id, filing_id, number, request_text, evidence_needed, response_document_id, state

imm_case_events       (exists) + columns: filing_id, from_stage, to_stage, trigger, approval_id
```

`imm_cases` keeps its columns for compatibility; `receipt_number`, `filed_date`, `rfe_due_date`
are read from the primary filing from then on, and backfilled once.

## 4. Templates (data, not code)

`immigration_rules.yaml` grows stages and triggers per case type. Example:

```yaml
case_types:
  eb2_green_card:
    label: "EB-2 / EB-3 green card"
    filings: [PERM, I-140, I-485, I-765, I-131]
    gates: [conflict_check, engagement_signed]        # matter can't leave intake without them
    stages:
      - id: perm
        on: { document: eta9089_certification }
        deadline: { kind: i140_filing, days: 180, owner: attorney }
      - id: i140_filed
        on: { document: i797c_receipt, form: I-140 }
      - id: i140_approved
        on: { document: i797_approval, form: I-140 }
      - id: i485_filed
        on: { document: i797c_receipt, form: I-485 }
        approval: filing_package                       # attorney signs off before this stage
      - id: biometrics
        on: { document: i797c_appointment }
        deadline: { kind: biometrics, from_notice: appointment_date, owner: paralegal }
      - id: decision
        on: { document: i797_approval, form: I-485 }
        status_period: { kind: LPR, years: 10 }
        next: [n400_eligibility]                        # opens a reminder for the next matter
    any_stage:
      - on: { document: i797e_rfe }
        to: rfe
        deadline: { kind: rfe_response, from_notice: response_due, owner: attorney, backup: paralegal,
                    remind_days: [30, 14, 7, 3, 1] }
      - on: { document: i797_denial }
        to: denied
        deadline: { kind: appeal, days: 30, owner: attorney }
```

Rules engine: a transition fires only when its trigger matches **and** the matter is in an earlier
stage; out-of-order notices are recorded as events and flagged, never silently applied.

## 5. Document → case events pipeline

Runs as the existing post-processing hook, after classification.

1. **Identify** the form and notice: form fields (filled PDFs), notice type (I-797C receipt /
   appointment / I-797 approval / I-797E RFE / denial), receipt number, dates.
2. **Find the person**: receipt number → filing (exact) → A-number → name + DOB → name.
   No match → the existing "new person" approval.
3. **Find the filing**: receipt number, else the matter's open filing of that form.
   None → propose a matter from the template that lists this form (approval).
4. **Apply the template trigger** → stage change, deadline(s), status period, evidence ticked.
5. **Cross-check** the facts against the person: name spelling, DOB, A-number, passport number,
   I-94 dates. Mismatch → a review task on the matter ("DOB on I-94 differs from passport").
6. **Log** every change as an `imm_case_events` row with the source document.

Anything with legal weight goes through an approval instead of applying directly (section 7).

## 6. Deadline engine

- Deadlines are **rows**, created by rules, notices or people; never recomputed silently.
- A daily job: open deadlines within each `remind_days` → notification to owner; within 3 days
  and not acknowledged → backup owner and the supervising attorney; past due → `missed` + alert.
- Docket views: per attorney (next 14 days), per matter, firm-wide. Calendar export (ICS).
- Completing a deadline needs evidence: e.g. an RFE deadline is done when a response document
  is attached to the filing, not by ticking a box.

## 7. Human approvals (gates)

Reuses `approval_requests` with an `action` payload (built for "create client").

| Gate | Raised when | Approver | On approve |
|---|---|---|---|
| New person | A document names someone unknown | Attorney or paralegal | Person + client created, documents linked |
| New matter | A notice/form has no matching matter | Attorney | Matter from template, filings, evidence list |
| Engagement | Matter leaves intake | Attorney | Conflict check recorded; engagement letter signed |
| Filing package | Stage requires `approval: filing_package` | Attorney | Package frozen (hash), filing → filed |
| RFE response | Response assembled | Attorney | Filing → rfe_responded, deadline done |
| Out-of-order notice | Trigger arrives for a later stage | Paralegal | Stage applied, gap noted |

Approval cards appear on the page they concern (client list banner, matter page), not only on
the Approvals page.

## 8. Roles and access

- **Attorney**: everything in their matters; sole approver for engagement, filing package, RFE.
- **Paralegal**: prepare, collect, upload, propose; cannot approve legal-weight gates.
- **Client / employer HR** (portal, token links as intake today): their own evidence requests and
  uploads, e-signature; never other parties' data.
- Every view and change is audited (existing audit trail); filed packages are immutable.

## 9. API (new or changed)

```
GET  /api/immigration/people?q=            search people (name, A-number, receipt)
GET  /api/immigration/people/{id}           person + status periods + matters
GET  /api/immigration/matters/{id}          matter + parties + filings + evidence + deadlines + events
POST /api/immigration/matters               from template (case_type, beneficiary_person_id, …)
POST /api/immigration/filings/{id}/evidence/{item}   state change, attach document
GET  /api/immigration/deadlines?owner=&days=        the docket
POST /api/immigration/deadlines/{id}/done           with the proving document
GET  /api/immigration/inbox                 pending approvals + review tasks for this user
```

Existing `/api/immigration/cases…` endpoints stay and read through the new model.

## 10. Screens (mocked up separately)

Clients (with new-people banner) · Client/person page (status timeline, matters, documents) ·
Matter page (stage bar per filing, evidence, deadlines, events) · Docket · Case templates.

## 11. Delivery plan

| Phase | Scope | Done when |
|---|---|---|
| 1 | V74 tables, backfill from `imm_cases`/`client_immigration`, people + parties + filings + status periods | Every existing case shows its people and filings; tests on demo tenant |
| 2 | Template stages/triggers, notice parsing, pipeline steps 1–6, deadline rows | Uploading an I-797C / RFE / approval moves the right filing and creates deadlines |
| 3 | Deadline job, reminders, escalation, docket API | Missed-deadline test passes; ICS export |
| 4 | Evidence states, client/employer requests, cross-document checks | Mismatch on a test set raises review tasks |
| 5 | Gates: engagement, filing package (frozen hash), RFE response | Can't file without attorney sign-off |
| 6 | Screens on top (Clients, Person, Matter, Docket, Templates) | Demo flow end to end in the UI |

## 12. Open questions

1. Is the **client** the beneficiary, the petitioner, or the employer? Proposal: the person is
   always a party; the *client* (who engaged the firm) is set on the matter.
2. USCIS case-status checks: the public status page has no API for firms; scrape with consent
   per receipt, or leave status to uploaded notices only? Proposal: notices only in phase 2.
3. Which case types first? Proposal: H-1B extension/transfer, EB-2/3 green card, family I-130/I-485,
   N-400 — they cover most firm volume.
4. Reminder delivery beyond the app (email/SMS) — off by default, per firm setting.
