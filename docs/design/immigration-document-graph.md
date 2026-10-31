# Immigration document graph — design

Status: **draft for review** · Author: Vijay Kumar Bomma · Oct 2026
Companion to `immigration-case-system.md` (matters, filings, deadlines).

Rules below are general practice used to shape the model. An attorney confirms each rule in the
rules file before it raises alerts; nothing here is legal advice.

## 1. Idea

One graph per client household. **Nodes** are documents and the facts read from them; **edges**
are typed relationships. Each visa type is a **pattern** over the same node and edge types, kept as
data. The system proposes nodes and edges from uploaded documents, checks them against the
pattern, and shows what is missing or inconsistent. The lawyer can accept, correct or override
anything; every change is kept with who, when and why.

```
person ──holds──▶ passport ──travels on──▶ visa stamp ──admitted as──▶ I-94 ──grants──▶ status period
   │                                                                                   ▲
   ├──beneficiary of──▶ filing (I-129 …) ──requires──▶ LCA                             │
   │                         └──decided by──▶ I-797 approval ──grants────────────────────┘
   └──spouse of / child of──▶ person (dependent) ──derives from──▶ principal's status period
```

## 2. Node types (about 25 cover all 30 visa types)

| Group | Node types |
|---|---|
| Identity | person, passport, birth certificate, marriage certificate, divorce decree, national ID |
| Entry & stay | visa stamp, I-94, entry/exit (travel history), status period (derived) |
| Labor | LCA (ETA-9035), prevailing wage (ETA-9141), PERM (ETA-9089), job offer / support letter |
| USCIS filings | form (I-129, I-130, I-140, I-485, I-539, I-765, I-131, I-751, N-400, I-90, I-526, I-589, I-821D, I-129F, I-20/DS-2019 issued by school/sponsor) |
| USCIS notices | I-797C receipt, I-797C appointment, I-797 approval, I-797E RFE, NOID, denial, transfer notice |
| Cards | EAD, advance parole, green card, naturalization certificate |
| Evidence | degree, credential evaluation, pay stubs, tax returns, experience letters, award/publication (EB-1/O-1), investment records (EB-5), joint-life evidence (marriage cases) |
| Organisation | employer, school, sponsor (J-1), investment enterprise (EB-5) |

Each node stores its **facts** (name, DOB, A-number, receipt number, valid from/to, class, SOC code,
wage, address …) with the source page for each fact, as the app already does for citations.

## 3. Edge types

| Edge | Meaning | Example |
|---|---|---|
| `identifies` | proves who the person is | passport → person |
| `requires` | can't be filed/valid without | I-129 → LCA; I-485 → I-140 approved (or concurrent) |
| `decides` | notice is the outcome of a filing | I-797 approval → I-129 |
| `grants` | creates a status period | I-797 approval, I-94 → H-1B status 15 Feb 2024–14 Feb 2027 |
| `derives_from` | dependent status follows a principal | H-4 status → H-1B status |
| `supersedes` | a newer document replaces an older | new I-94 → old I-94; extension approval → prior approval |
| `same_as` | two uploads are the same document | duplicate scan, colour vs. B/W copy |
| `belongs_to` | the document's person | every node → person |
| `enables` | unlocks an optional filing | I-140 approved → H-1B past 6 years; → H-4 EAD |
| `conflicts_with` | stored when a check fails | I-94 DOB ≠ passport DOB |

Edges have `state`: **proposed** (system), **confirmed** (lawyer or exact match), **rejected**,
**overridden** (kept but a rule is waived with a reason).

## 4. How edges are proposed

In order of confidence; anything below *exact* stays **proposed** until confirmed.

1. **Exact keys**: receipt number (notice ↔ filing), A-number, passport number, SEVIS ID,
   LCA case number, PERM case number, I-94 admission number. → `confirmed` automatically.
2. **Person match**: name + DOB (tolerant of order, middle names, diacritics).
3. **Pattern position**: an I-797 approval for class H-1B, same person, dates overlapping an
   I-129 matter → `decides` that I-129.
4. **Dates**: a newer document of the same type and person → `supersedes`.

## 5. Checks (where mismatches come from)

Run whenever a node or edge changes. Each check is data: `{applies_to, rule, severity, explain}`.

- **Identity consistency**: name, DOB, country of birth, A-number identical on every node of a person.
- **Key consistency**: receipt number on notice = filing; LCA/PERM case number on the petition.
- **Date logic**: I-94 admit-until ≤ / = approval end date (flag when they differ — I-94 governs);
  filing date within the filing window; status periods continuous (gaps flagged).
- **Requirement completeness**: every `requires` edge of the pattern has a confirmed node.
- **Content consistency**: job title, SOC code, worksite and wage the same across LCA, I-129 and
  support letter; employer FEIN the same everywhere; wage ≥ LCA/prevailing wage.
- **Dependents**: dependent status end ≤ principal's; H-4 EAD only with an `enables` I-140.
- **History**: addresses, employers, trips and marital history don't contradict prior filings.

Severity: **blocker** (would cause a denial/RFE or a status problem), **warning**, **info**.

## 6. Lawyer controls (edit and adjust)

Every proposal is editable; nothing is hidden behind the machine.

| Action | Effect | Kept in audit |
|---|---|---|
| Confirm / reject an edge | `state` changes; checks rerun | who, when |
| Re-link | point a notice to another filing or person | old → new |
| Correct a fact | lawyer value wins over extracted value; source still shown | old value, new value, reason |
| Mark the source wrong | e.g. "I-94 has CBP error — correction requested" → opens a task | reason |
| Waive a check | `overridden` with reason ("name order differs by consulate convention") | reason, expiry optional |
| Add a missing node | upload or "not applicable" with reason | reason |
| Edit the pattern for this matter | add/remove a required document for this case only | reason |
| Propose a firm-wide rule change | goes to an admin for approval; versioned | diff |

Overrides never delete the original extraction: the graph shows both values and who chose which.

## 7. Visa-type patterns (catalog, 30 types)

Pattern = required nodes and edges + the status it grants + what it enables next.
`→` = requires · `⇒` = grants · `↳` = derives from · `+` = enables.

| # | Type | Core chain (requires) | Grants / derives | Enables / watch |
|---|---|---|---|---|
| 1 | H-1B new (cap) | registration selection → LCA → I-129 → I-797 | ⇒ H-1B, 3 yrs | change of status or consular visa; cap-gap for F-1 |
| 2 | H-1B extension | prior I-797 + I-94 → LCA → I-129 | ⇒ H-1B to 6-yr max | + past 6 yrs with I-140 / PERM pending 365+ days |
| 3 | H-1B transfer | current I-797, pay stubs → new LCA → I-129 | ⇒ H-1B with new employer | portability on receipt |
| 4 | H-1B amendment | worksite change → new LCA → I-129 | ⇒ updated H-1B | worksite on LCA = actual |
| 5 | H-4 | marriage/birth cert → principal H-1B → I-539 or consular | ↳ H-1B | ends with principal |
| 6 | H-4 EAD | H-4 status → I-765 | ⇒ EAD | needs principal's approved I-140 (or H-1B AC21 extension) |
| 7 | L-1A | 1 yr abroad at affiliate, org charts → I-129 (or blanket) | ⇒ L-1A, 7-yr max | + EB-1C |
| 8 | L-1B | specialized-knowledge evidence → I-129 | ⇒ L-1B, 5-yr max | |
| 9 | L-2 | marriage cert → principal L-1 | ↳ L-1 | spouse work-authorized incident to status (I-94 annotation) |
| 10 | O-1A / O-1B | extraordinary-ability evidence, advisory opinion → I-129 | ⇒ O-1, 3 yrs | + EB-1A |
| 11 | O-3 | relationship docs → principal O-1 | ↳ O-1 | |
| 12 | TN | Canadian/Mexican citizenship, USMCA profession, offer letter → CBP or I-129 | ⇒ TN, 3 yrs | |
| 13 | TD | relationship docs → principal TN | ↳ TN | |
| 14 | E-3 | Australian citizenship, LCA → consular or I-129 | ⇒ E-3, 2 yrs | |
| 15 | E-2 | investment, treaty nationality, business plan → DS-160/consular or I-129 | ⇒ E-2 | E-2 spouse work-authorized |
| 16 | F-1 | I-20 (SEVIS ID), funds → visa → I-94 D/S | ⇒ F-1 D/S | + OPT; cap-gap |
| 17 | OPT | I-20 with DSO recommendation → I-765 | ⇒ EAD 12 mo | 90-day unemployment limit |
| 18 | STEM OPT | STEM degree, E-Verify employer, I-983 → I-765 | ⇒ EAD 24 mo | 150-day unemployment total |
| 19 | J-1 | DS-2019 (sponsor) → visa | ⇒ J-1 D/S | 2-yr home residency (212(e)) → waiver |
| 20 | J-2 | relationship → principal J-1 → I-765 optional | ↳ J-1 | |
| 21 | B-1/B-2 extension | I-94 → I-539 | ⇒ extended B status | file before I-94 ends |
| 22 | R-1 | religious org, ministry evidence → I-129 | ⇒ R-1, 5-yr max | + special immigrant religious worker |
| 23 | EB-1A | extraordinary-ability evidence → I-140 (self) | priority date | → I-485 / consular |
| 24 | EB-1B / EB-1C | offer + research record / multinational manager → I-140 | priority date | → I-485 |
| 25 | EB-2 / EB-3 (PERM) | ETA-9141 → PERM ETA-9089 → I-140 | priority date | → I-485 + I-765 + I-131 when current |
| 26 | EB-2 NIW | national-interest evidence → I-140 (self) | priority date | → I-485 |
| 27 | EB-5 | investment & source-of-funds → I-526E | conditional resident via I-485 | → I-829 to remove conditions |
| 28 | Family (I-130 → I-485 / consular) | petitioner status, relationship evidence → I-130 → I-864 → I-485 | ⇒ green card (CR-1 if marriage < 2 yrs) | → I-751 if conditional |
| 29 | K-1 / K-3 | meeting evidence → I-129F → consular → marriage within 90 days → I-485 | ⇒ green card (conditional) | → I-751 |
| 30 | I-751 / N-400 / I-90 | conditional card + joint evidence → I-751; LPR 5 yrs (3 if married to citizen) → N-400; card → I-90 | ⇒ 10-yr card / citizenship / new card | N-400: continuous residence, physical presence, trips |

Humanitarian types (asylum I-589, TPS I-821, DACA I-821D, U/T visas, VAWA) fit the same model
and are added later; they carry stricter confidentiality defaults.

## 8. Storage

```
imm_nodes   id, tenant_id, household_id, person_id, node_type, document_id (nullable for derived),
            facts jsonb {field: {value, source_page, extracted_value, set_by, set_at}}, valid_from, valid_to
imm_edges   id, tenant_id, from_node, to_node, edge_type, state, confidence, proposed_by,
            decided_by, decided_at, reason
imm_checks  id, tenant_id, check_key, node_ids, edge_ids, severity, message, state: open|resolved|waived,
            waived_by, reason, created_at
imm_patterns  versioned YAML in app/services/immigration/patterns/*.yaml (one per visa type),
            approved by an attorney; per-matter overrides in imm_matter_pattern_overrides
```

These sit beside the tables in the case-system design: a **filing** is a node of type form with its
notices linked by `decides`; a **status period** is a derived node created by `grants`.

## 9. What the lawyer sees

- **Client page → Relationships tab**: the household graph laid out by chain (identity, work
  status, green card, family). Nodes coloured by state: confirmed, proposed, missing, conflict.
- **Conflicts panel**: each open check in plain words, with both values side by side, their sources,
  and buttons: *Use passport value* · *Use I-94 value* · *Correct…* · *Waive with reason* ·
  *Open correction task*.
- **Proposed links**: one-click confirm / re-link / reject, with the reason it was proposed.
- **Pattern view**: the visa type's required nodes with ticks; "not applicable" needs a reason.

## 10. Delivery

1. Node/edge/check tables; build nodes from documents already extracted; exact-key edges.
2. Patterns for the 8 highest-volume types (#1–6, 16–18, 25, 28) and the identity/date checks.
3. Lawyer controls (confirm, re-link, correct, waive) with audit; conflicts panel.
4. Remaining patterns; content-consistency checks (LCA ↔ I-129 ↔ letter); history checks.
5. Relationships tab (graph view) on the client page.
