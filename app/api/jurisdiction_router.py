"""
Jurisdiction Engine — config-driven insurance rules per country/state/city.
Rules are data, not code. Adding a new jurisdiction = inserting rows.
"""
import uuid, json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.database import get_db

router = APIRouter(prefix="/api/jurisdictions", tags=["jurisdictions"])

# ── Seed data — real US state insurance requirements ─────────────────────────
# auto_min: [BI_per_person, BI_per_accident, PD, PIP, UM_per_person, UM_per_accident]
# All values in USD thousands (15 = $15,000).  None = not required.
# pip_required: state mandates PIP (no-fault)
# no_fault: true no-fault state (PIP replaces tort for minor injuries)
# sr22: state uses SR-22  fr44: state uses FR-44 instead/also
# cancel_nonpayment: days notice for non-payment cancellation
# cancel_underwriting: days notice for underwriting cancellation

US_STATES = [
  {"code":"AL","name":"Alabama",        "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"AK","name":"Alaska",         "auto_min":[50,100,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":20,"notes":""},
  {"code":"AZ","name":"Arizona",        "auto_min":[25,50,15,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"AR","name":"Arkansas",       "auto_min":[25,50,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"CA","name":"California",     "auto_min":[15,30,5,None,None,None], "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":20,"notes":"Proposition 103 rate regulation. CLCA available for low-income."},
  {"code":"CO","name":"Colorado",       "auto_min":[25,50,15,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"MedPay recommended but not required."},
  {"code":"CT","name":"Connecticut",    "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":15,"cancel_uw":30,"notes":"UM/UIM required."},
  {"code":"DE","name":"Delaware",       "auto_min":[25,50,10,15,None,None], "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":15,"cancel_uw":30,"notes":"PIP $15k required. Limited tort default."},
  {"code":"FL","name":"Florida",        "auto_min":[None,None,10,10,None,None],"pip_req":True,"no_fault":True,"sr22":False,"fr44":True,"cancel_np":15,"cancel_uw":45,"notes":"PIP $10k + PDL $10k only required. FR-44 for DUI (100/300/50). No BI requirement. No-fault state."},
  {"code":"GA","name":"Georgia",        "auto_min":[25,50,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"HI","name":"Hawaii",         "auto_min":[20,40,10,10,None,None], "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"PIP $10k. No-fault state. SR-1P form used."},
  {"code":"ID","name":"Idaho",          "auto_min":[25,50,15,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"IL","name":"Illinois",       "auto_min":[25,50,20,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM required. UIM required."},
  {"code":"IN","name":"Indiana",        "auto_min":[25,50,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"IA","name":"Iowa",           "auto_min":[20,40,15,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"KS","name":"Kansas",         "auto_min":[25,50,25,4.5,25,50],    "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"PIP $4.5k. No-fault state. UM/UIM required."},
  {"code":"KY","name":"Kentucky",       "auto_min":[25,50,25,10,None,None], "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":14,"cancel_uw":30,"notes":"No-fault. Can reject no-fault verbally. PIP $10k."},
  {"code":"LA","name":"Louisiana",      "auto_min":[15,30,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"High-risk state. Bad faith penalties."},
  {"code":"ME","name":"Maine",          "auto_min":[50,100,25,2,50,100],    "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"MedPay $2k required. UM/UIM required."},
  {"code":"MD","name":"Maryland",       "auto_min":[30,60,15,2.5,30,60],    "pip_req":True, "no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":45,"notes":"PIP $2.5k. UM/UIM required. Can waive PIP."},
  {"code":"MA","name":"Massachusetts",  "auto_min":[20,40,5,8,20,40],       "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":20,"cancel_uw":45,"notes":"PIP $8k. No-fault. UM/UIM required. State-regulated rates."},
  {"code":"MI","name":"Michigan",       "auto_min":[50,100,10,None,None,None],"pip_req":True,"no_fault":True,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"PIP unlimited/tiered options post-2020 reform. Highest minimums. Mini-tort $3k."},
  {"code":"MN","name":"Minnesota",      "auto_min":[30,60,10,40,25,50],     "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"PIP $40k. No-fault. UM/UIM required."},
  {"code":"MS","name":"Mississippi",    "auto_min":[25,50,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"MO","name":"Missouri",       "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM required."},
  {"code":"MT","name":"Montana",        "auto_min":[25,50,20,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"NE","name":"Nebraska",       "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM/UIM required."},
  {"code":"NV","name":"Nevada",         "auto_min":[25,50,20,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"NH","name":"New Hampshire",  "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"Only state with no mandatory auto insurance — but must prove financial responsibility."},
  {"code":"NJ","name":"New Jersey",     "auto_min":[15,30,5,15,15,30],      "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":15,"cancel_uw":30,"notes":"No-fault. Basic vs standard policy. PIP $15k. Limited tort default."},
  {"code":"NM","name":"New Mexico",     "auto_min":[25,50,10,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"NY","name":"New York",       "auto_min":[25,50,10,50,25,50],     "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":15,"cancel_uw":45,"notes":"No-fault. PIP $50k (Basic Economic Loss). SUM required. NY specific endorsements required."},
  {"code":"NC","name":"North Carolina", "auto_min":[30,60,25,None,30,60],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM/UIM required. NCIUA assigned risk."},
  {"code":"ND","name":"North Dakota",   "auto_min":[25,50,25,30,25,50],     "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"No-fault. PIP $30k."},
  {"code":"OH","name":"Ohio",           "auto_min":[25,50,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"OK","name":"Oklahoma",       "auto_min":[25,50,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"OR","name":"Oregon",         "auto_min":[25,50,20,15,25,50],     "pip_req":True, "no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"PIP $15k required. UM/UIM required. Not true no-fault."},
  {"code":"PA","name":"Pennsylvania",   "auto_min":[15,30,5,5,None,None],   "pip_req":True, "no_fault":False,"sr22":True, "fr44":False,"cancel_np":15,"cancel_uw":30,"notes":"Limited tort / full tort choice. First-party benefits (PIP) $5k min. Can waive."},
  {"code":"RI","name":"Rhode Island",   "auto_min":[25,50,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"SC","name":"South Carolina", "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":15,"cancel_uw":30,"notes":"UM/UIM required."},
  {"code":"SD","name":"South Dakota",   "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM/UIM required."},
  {"code":"TN","name":"Tennessee",      "auto_min":[25,50,15,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"TX","name":"Texas",          "auto_min":[30,60,25,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"PIP offered but not required. Windstorm separate in coastal counties (TWIA)."},
  {"code":"UT","name":"Utah",           "auto_min":[25,65,15,3,None,None],  "pip_req":True, "no_fault":True, "sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"No-fault. PIP $3k."},
  {"code":"VT","name":"Vermont",        "auto_min":[25,50,10,None,50,100],  "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM/UIM required at high limits."},
  {"code":"VA","name":"Virginia",       "auto_min":[30,60,20,None,None,None],"pip_req":False,"no_fault":False,"sr22":False,"fr44":True,"cancel_np":10,"cancel_uw":30,"notes":"FR-44 for DUI (requires 50/100/40). Can pay $500 Uninsured Motor Vehicle fee instead of insurance."},
  {"code":"WA","name":"Washington",     "auto_min":[25,50,10,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":20,"cancel_uw":45,"notes":"PIP available as add-on. Long cancellation notice rules."},
  {"code":"WV","name":"West Virginia",  "auto_min":[25,50,25,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM/UIM required."},
  {"code":"WI","name":"Wisconsin",      "auto_min":[25,50,10,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM required."},
  {"code":"WY","name":"Wyoming",        "auto_min":[25,50,20,None,None,None],"pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":""},
  {"code":"DC","name":"Washington DC",  "auto_min":[25,50,10,None,25,50],   "pip_req":False,"no_fault":False,"sr22":True, "fr44":False,"cancel_np":10,"cancel_uw":30,"notes":"UM required."},
]

# ── International markets ─────────────────────────────────────────────────────
# currency: ISO 4217  regulator: primary insurance regulator
# tp_required: third-party/liability mandatory  no_fault: true no-fault elements
# cancellation_days: standard notice period
COUNTRIES_INTL = [
  # --- EUROPE ---
  {"code":"GB","name":"United Kingdom","region":"Europe","currency":"GBP","regulator":"FCA / PRA",
   "tp_required":True,"no_fault":False,"cancellation_days":7,
   "lobs":["motor","home","life","health","commercial","employers_liability","professional_indemnity","cyber"],
   "motor_notes":"Third-party minimum mandated by Road Traffic Act 1988. MIB compensates victims of uninsured drivers. Excess (deductible) structure common. Telematics/black-box widely used.",
   "key_rules":"Employers Liability (Compulsory Insurance) Act 1969 requires £5M minimum cover. FCA Consumer Duty applies from 2023."},
  {"code":"DE","name":"Germany","region":"Europe","currency":"EUR","regulator":"BaFin",
   "tp_required":True,"no_fault":False,"cancellation_days":30,
   "lobs":["Kfz (motor)","Haftpflicht (liability)","Lebensversicherung (life)","Kranken (health)","Rechtsschutz (legal)","Wohngebäude (property)","Cyber"],
   "motor_notes":"Kfz-Haftpflicht mandatory. Regional SF-Klasse bonus-malus system. Minimum: €7.5M personal injury, €1.12M property, €50k financial loss.",
   "key_rules":"Annual cancellation right (Nov 30 deadline). VVG (Insurance Contract Act) governs. GDV (German Insurers) standards apply."},
  {"code":"FR","name":"France","region":"Europe","currency":"EUR","regulator":"ACPR / ORIAS",
   "tp_required":True,"no_fault":False,"cancellation_days":15,
   "lobs":["Auto","Habitation","Santé","Prévoyance","RC Pro","Cyber","MRH"],
   "motor_notes":"RC Auto mandatory. Bonus-malus coefficient system (0.50–3.50). Minimum: unlimited personal injury. IDA convention for amicable settlement.",
   "key_rules":"Loi Hamon (2015) allows mid-term cancellation after 1 year. Loi Chatel requires renewal notice. Fonds de Garantie covers uninsured."},
  {"code":"ES","name":"Spain","region":"Europe","currency":"EUR","regulator":"DGSFP",
   "tp_required":True,"no_fault":False,"cancellation_days":30,
   "lobs":["Autos","Hogar","Salud","Vida","RC","Decesos","Cyber"],
   "motor_notes":"Seguro de RC obligatorio. Minimum: €70M personal injury, €15M property per incident. Baremo (scale) for bodily injury compensation.",
   "key_rules":"LRCSCVM regulates auto compensation. Consorcio de Compensación covers extraordinary risks (floods, terrorism)."},
  {"code":"IT","name":"Italy","region":"Europe","currency":"EUR","regulator":"IVASS",
   "tp_required":True,"no_fault":False,"cancellation_days":60,
   "lobs":["RC Auto","Casa","Vita","Salute","RC Professionale","Cyber"],
   "motor_notes":"Assicurazione RC Auto mandatory. Minimum: €6.07M personal injury, €1.22M property. CID (Convenzione Indennizzo Diretto) for direct settlement. Class/merit system.",
   "key_rules":"Codice delle Assicurazioni Private (CAP). IVASS supervises conduct. Anti-fraud measures mandatory."},
  {"code":"NL","name":"Netherlands","region":"Europe","currency":"EUR","regulator":"DNB / AFM",
   "tp_required":True,"no_fault":False,"cancellation_days":30,
   "lobs":["Motorrijtuigverzekering","Woonverzekering","Levensverzekering","Zorgverzekering","Bedrijfsaansprakelijkheid","Cyber"],
   "motor_notes":"WA (liability) mandatory. Minimum: €1.22M property, €6.07M personal. No-claim discount system. Green card system for cross-border.",
   "key_rules":"Health insurance (Zorgverzekering) mandatory separately. WFT licensing required for brokers."},
  {"code":"CH","name":"Switzerland","region":"Europe","currency":"CHF","regulator":"FINMA",
   "tp_required":True,"no_fault":False,"cancellation_days":60,
   "lobs":["Motorfahrzeug","Hausrat","Lebens","Kranken","Haftpflicht","Cyber"],
   "motor_notes":"Motor liability mandatory per SVG. CHF 100M minimum. Green card required. Per-canton variations in premium calculation.",
   "key_rules":"VVG (Insurance Contract Act) governs. FINMA supervises solvency. Basic health insurance mandatory under KVG."},

  # --- NORTH AMERICA ---
  {"code":"CA","name":"Canada","region":"North America","currency":"CAD","regulator":"Provincial (FSRA/AMF/BCFSA/AIC)",
   "tp_required":True,"no_fault":True,"cancellation_days":15,
   "lobs":["Auto","Home","Life","Health","Commercial","Farm","Cyber","Title"],
   "motor_notes":"Provincially regulated. Ontario: FSRA, standard OAP 1 policy, OPCF endorsements, Direct Compensation-Property Damage (DCPD). Quebec: public auto (SAAQ) for bodily injury, private for property. BC: ICBC public monopoly. Alberta: private market.",
   "key_rules":"No-fault accident benefits mandatory in most provinces. FRSA (Canada) regulates federally chartered insurers. Anti-fraud IBC guidelines."},
  {"code":"MX","name":"Mexico","region":"North America","currency":"MXN","regulator":"CNSF",
   "tp_required":True,"no_fault":False,"cancellation_days":30,
   "lobs":["Autos","Gastos Médicos","Vida","Daños","RC","Fianzas","Agro"],
   "motor_notes":"RC Civil (third party) mandatory per Ley de Caminos. Minimum MXN 3M for property, MXN 3M bodily injury. Tourist auto policies available at US border. Adjuster (perito) required for claims.",
   "key_rules":"CNSF regulates per Ley General de Instituciones de Seguros. Serie S regulations for products. CONDUSEF for consumer complaints."},

  # --- ASIA-PACIFIC ---
  {"code":"AU","name":"Australia","region":"Asia-Pacific","currency":"AUD","regulator":"APRA / ASIC",
   "tp_required":True,"no_fault":True,"cancellation_days":14,
   "lobs":["CTP","Home & Contents","Life","Health","Business Package","Farm","Cyber","Marine","Travel"],
   "motor_notes":"CTP (Compulsory Third Party / Green Slip) mandatory, state-run. NSW: iCare/private insurers. QLD: MACA scheme. VIC: TAC public monopoly. Comprehensive/third-party voluntary. Excess = deductible. ICA code of practice.",
   "key_rules":"General Insurance Code of Practice 2021. APRA capital standards (GPS 110). Unfair contract terms apply. ASIC DDO (design & distribution obligations)."},
  {"code":"IN","name":"India","region":"Asia-Pacific","currency":"INR","regulator":"IRDAI",
   "tp_required":True,"no_fault":True,"cancellation_days":15,
   "lobs":["Motor TP","Motor OD","Health","Life","Fire","Marine","Crop","Cyber","PA"],
   "motor_notes":"Motor Third Party mandatory per Motor Vehicles Act 1988. TP premium tariffed by IRDAI. Own Damage (OD) voluntary. Bundled 3-year/5-year policies for new vehicles. IMT endorsements. Cashless garages nationwide.",
   "key_rules":"IRDAI (Insurance Regulatory and Development Authority) sets tariffs, solvency, product approval. Surveyor mandatory for claims >INR 50,000. Ombudsman scheme."},
  {"code":"SG","name":"Singapore","region":"Asia-Pacific","currency":"SGD","regulator":"MAS",
   "tp_required":True,"no_fault":False,"cancellation_days":15,
   "lobs":["Motor","Home","Life","Health","Travel","Business","Directors & Officers","Cyber","Marine"],
   "motor_notes":"Third-party mandatory per Motor Vehicles (Third-Party Risks and Compensation) Act. GIA motor tariff abolished 2002 (free pricing). NCB (No-Claim Bonus) up to 50%. OA (Own Accident) optional. MAS 124 capital framework.",
   "key_rules":"Financial Advisers Act for brokers/agents. MAS TIA (Technology Risk) guidelines. PDPA data protection mandatory for insurers."},
  {"code":"JP","name":"Japan","region":"Asia-Pacific","currency":"JPY","regulator":"FSA / MLIT",
   "tp_required":True,"no_fault":True,"cancellation_days":30,
   "lobs":["自動車保険 (Jidousha)","火災 (Kasai / Fire)","生命 (Seimei / Life)","医療 (Iryo / Medical)","賠償 (Baisho / Liability)","Cyber","Travel"],
   "motor_notes":"Jidousha Songai Baisho Hosho (compulsory auto / shakken). No-fault for bodily injury, fault-based for property. Optional comprehensive (任意保険). Rating system by zone/age/type.",
   "key_rules":"Insurance Business Act (保険業法). FSA supervises solvency. Consumer Contract Act governs policyholder rights. Long-term auto policies common (2–3 years)."},
  {"code":"HK","name":"Hong Kong","region":"Asia-Pacific","currency":"HKD","regulator":"IA (Insurance Authority)",
   "tp_required":True,"no_fault":False,"cancellation_days":7,
   "lobs":["Motor","Property","Life","Medical","Employees Compensation","Travel","Marine","Cyber","D&O"],
   "motor_notes":"Third-party mandatory per Motor Vehicles Insurance (Third Party Risks) Ordinance. No-fault elements for employees. ECF (Employees Compensation Fund). IA OCI (Outcome-Competency Initiative) for advisers.",
   "key_rules":"Employees Compensation Ordinance mandatory. IIQAS licensing for intermediaries. ORSO for occupational retirement. IA launched FIVRS for digital submissions."},
  {"code":"AE","name":"UAE","region":"Middle East","currency":"AED","regulator":"CBUAE / IA UAE",
   "tp_required":True,"no_fault":False,"cancellation_days":15,
   "lobs":["Motor","Medical","Life Takaful","Property","Marine","Workmen Compensation","Cyber","Aviation"],
   "motor_notes":"Third-party mandatory per Federal Law 6/2007. Minimum AED 250k property, bodily injury unlimited. UAE unified motor policy (UMP) standard wording. Agency repair for new vehicles.",
   "key_rules":"Health insurance mandatory (Dubai: DHA, Abu Dhabi: HAAD/DoH). Takaful (Islamic insurance) widely used. IFRS 17 adopted. 1-year policies standard, up to 3-year available."},

  # --- LATIN AMERICA ---
  {"code":"BR","name":"Brazil","region":"Latin America","currency":"BRL","regulator":"SUSEP / ANS",
   "tp_required":True,"no_fault":True,"cancellation_days":30,
   "lobs":["Auto","Residencial","Vida","Saúde","RC","D&O","Cyber","Garantia","Agro","Transporte"],
   "motor_notes":"DPVAT (compulsory TP) replaced by SPVAT 2025 — covers bodily injury. Comprehensive (casco) voluntary. Tabela FIPE for vehicle values. Franquia = deductible. Bonus classes 0–10.",
   "key_rules":"SUSEP regulates general, life, open pension. ANS regulates health. LGPD (Brazil data protection) applies. CIRCULAR SUSEP 648 governs conduct. Arbitration widely used."},
  {"code":"CO","name":"Colombia","region":"Latin America","currency":"COP","regulator":"Superfinanciera",
   "tp_required":True,"no_fault":True,"cancellation_days":30,
   "lobs":["SOAT (auto TP)","Vida","Salud","Hogar","RC","Transporte","Agro","Cyber"],
   "motor_notes":"SOAT (Seguro Obligatorio de Accidentes de Tránsito) mandatory no-fault for bodily injury. Annual renewal at Simit points. Indemnity limits set by Superfinanciera. Comprehensive voluntary.",
   "key_rules":"Estatuto Orgánico del Sistema Financiero. Circular Básica Jurídica. Fonsat (SOAT fund) for uninsured. SFC Consumer Protection Circular."},

  # --- AFRICA ---
  {"code":"ZA","name":"South Africa","region":"Africa","currency":"ZAR","regulator":"FSCA / PA",
   "tp_required":True,"no_fault":True,"cancellation_days":30,
   "lobs":["Motor","Household Contents","Buildings","Life","Short-term Commercial","Crop","Cyber","Liability"],
   "motor_notes":"RAF (Road Accident Fund) compulsory no-fault — funded via fuel levy, not premium. Comprehensive vehicle insurance voluntary. SASRIA covers riot/political violence. Excess (deductible) standard.",
   "key_rules":"FAIS Act for advisers/brokers. POPIA (data protection) mandatory. TCF (Treating Customers Fairly) conduct standard. RDR (Retail Distribution Review) implementation ongoing. SAM (SA Solvency Assessment) capital regime."},
  {"code":"NG","name":"Nigeria","region":"Africa","currency":"NGN","regulator":"NAICOM",
   "tp_required":True,"no_fault":False,"cancellation_days":30,
   "lobs":["Motor","Fire & Special Perils","Life","Health","Marine","Group Life","Agric","Cyber"],
   "motor_notes":"Third-party mandatory per Insurance Act 2003. Minimum NGN 1M property damage, unlimited bodily injury. MVTPL (Motor Vehicle Third Party Liability). Marine cargo mandatory for imports.",
   "key_rules":"NAICOM Guidelines on Market Conduct. No-premium-no-cover rule strictly enforced. Recapitalisation requirements 2024. ECOWAS Brown Card for cross-border."},

  # --- MIDDLE EAST / OTHER ---
  {"code":"SA","name":"Saudi Arabia","region":"Middle East","currency":"SAR","regulator":"SAMA",
   "tp_required":True,"no_fault":False,"cancellation_days":15,
   "lobs":["Motor","Medical","Life Takaful","Property","Marine","Engineering","Workmen Compensation","Cyber"],
   "motor_notes":"Third-party mandatory per NCC regulations. Najm (accident reporting authority). Wasel unified motor policy. Medical insurance mandatory for expatriates and nationals.",
   "key_rules":"SAMA (Saudi Central Bank) oversees insurance. Takaful (Islamic insurance) dominant. Vision 2030 expanding insurance penetration. IFRS 17 adopted 2023. Arabic policy wording required."},
  {"code":"KE","name":"Kenya","region":"Africa","currency":"KES","regulator":"IRA Kenya",
   "tp_required":True,"no_fault":False,"cancellation_days":30,
   "lobs":["Motor","Health","Life","Fire","Marine","Engineering","Crop","Cyber","Liability"],
   "motor_notes":"Third-party mandatory per Insurance Act Cap 487. PSV (passenger vehicles) require additional cover. Motor Comprehensive popular for new vehicles. Valuation mandatory for vehicles over 5 years.",
   "key_rules":"IRA (Insurance Regulatory Authority) supervises. No-premium-no-cover rule. COMESA Yellow Card for East African cross-border. Digital insurance platforms (M-PESA integrated policies) common."},
]

# Lines of business available per state (auto is universal; others vary)
LOB_GLOBAL = [
  "auto","motorcycle","boat","rv","atv","snowmobile","classic_car","travel_trailer",
  "renters","homeowners","mobile_home","condo","landlord",
  "life","health","dental","vision","travel","telemedicine","identity_theft",
  "commercial_auto","commercial_property","general_liability","bop","workers_comp",
  "professional_liability","cyber","umbrella","flood","earthquake",
  "sr22","fr44","surety_bond","rideshare","mexican_auto",
]

# Per-LOB required fields that differ by state (config-driven)
LOB_FIELD_CONFIG = {
  "auto": {
    "always": ["year","make","model","vin","driver_name","dob","license_number","bi_per_person","bi_per_accident","pd"],
    "if_no_fault": ["pip_amount","pip_option"],
    "if_sr22":  ["sr22_state","sr22_reason","sr22_filing_date"],
    "if_fr44":  ["fr44_state","fr44_conviction_date"],
    "optional": ["um_per_person","um_per_accident","uim_per_person","uim_per_accident","comp_deductible","coll_deductible","rental","roadside"],
  },
  "homeowners": {
    "always": ["dwelling_address","year_built","construction_type","roof_type","dwelling_coverage","other_structures","personal_property","liability","medical_payments"],
    "coastal": ["wind_hail_deductible","hurricane_deductible","flood_separate"],
    "optional": ["earthquake","scheduled_personal_property","water_backup","identity_theft_rider"],
  },
  "workers_comp": {
    "always": ["employer_fein","class_codes","payroll_by_class","experience_mod","policy_limits"],
    "optional": ["stop_gap","employers_liability"],
  },
}

def ensure_tables(db: Session):
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS jurisdictions (
            id TEXT PRIMARY KEY,
            country_code TEXT NOT NULL DEFAULT 'US',
            country_name TEXT NOT NULL DEFAULT 'United States',
            state_code TEXT,
            state_name TEXT,
            city TEXT,
            config JSONB DEFAULT '{}',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(country_code, state_code, city)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS jurisdiction_products (
            id TEXT PRIMARY KEY,
            jurisdiction_id TEXT NOT NULL,
            lob TEXT NOT NULL,
            available BOOLEAN DEFAULT TRUE,
            required BOOLEAN DEFAULT FALSE,
            min_limits JSONB DEFAULT '{}',
            required_coverages JSONB DEFAULT '[]',
            required_endorsements JSONB DEFAULT '[]',
            agency_notes TEXT,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(jurisdiction_id, lob)
        )
    """))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS jurisdiction_forms (
            id TEXT PRIMARY KEY,
            jurisdiction_id TEXT NOT NULL,
            lob TEXT,
            form_code TEXT NOT NULL,
            form_name TEXT NOT NULL,
            trigger_condition TEXT DEFAULT 'always',
            filing_entity TEXT,
            deadline_days INTEGER,
            instructions TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """))
    db.commit()

def r2d(row): return dict(row._mapping) if row else None

def _seed_state(state: dict, db: Session):
    """Insert one US state into jurisdictions + jurisdiction_products."""
    existing = db.execute(text(
        "SELECT id FROM jurisdictions WHERE country_code='US' AND state_code=:sc"
    ), {"sc": state["code"]}).fetchone()

    am = state["auto_min"]  # [BI/person, BI/acc, PD, PIP, UM/person, UM/acc]
    config = {
        "no_fault": state["no_fault"],
        "pip_required": state["pip_req"],
        "sr22_available": state["sr22"],
        "fr44_available": state["fr44"],
        "cancellation_notice": {
            "nonpayment_days": state["cancel_np"],
            "underwriting_days": state["cancel_uw"],
        },
        "notes": state["notes"],
    }
    auto_limits = {
        "bi_per_person_k":   am[0],
        "bi_per_accident_k": am[1],
        "property_damage_k": am[2],
        "pip_k":             am[3],
        "um_per_person_k":   am[4],
        "um_per_accident_k": am[5],
    }

    if existing:
        jid = r2d(existing)["id"]
        db.execute(text("""
            UPDATE jurisdictions SET config=CAST(:cfg AS jsonb), updated_at=NOW()
            WHERE id=:id
        """), {"cfg": json.dumps(config), "id": jid})
    else:
        jid = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO jurisdictions (id, country_code, country_name, state_code, state_name, config)
            VALUES (:id,'US','United States',:sc,:sn,CAST(:cfg AS jsonb))
        """), {"id": jid, "sc": state["code"], "sn": state["name"],
               "cfg": json.dumps(config)})

    # Auto product limits
    db.execute(text("""
        INSERT INTO jurisdiction_products (id, jurisdiction_id, lob, available, required, min_limits, required_coverages)
        VALUES (:id, :jid, 'auto', TRUE, TRUE, CAST(:ml AS jsonb), CAST(:rc AS jsonb))
        ON CONFLICT (jurisdiction_id, lob) DO UPDATE
          SET min_limits=CAST(:ml AS jsonb), required_coverages=CAST(:rc AS jsonb), updated_at=NOW()
    """), {
        "id": str(uuid.uuid4()), "jid": jid,
        "ml": json.dumps(auto_limits),
        "rc": json.dumps(["bi","pd"] + (["pip"] if state["pip_req"] else [])
                         + (["um"] if am[4] else []))
    })

    # SR-22 form
    if state["sr22"]:
        db.execute(text("""
            INSERT INTO jurisdiction_forms (id, jurisdiction_id, lob, form_code, form_name, trigger_condition, filing_entity, deadline_days, instructions)
            VALUES (:id,:jid,'auto','SR-22','SR-22 Certificate of Financial Responsibility',
                    'on_sr22_required','DMV',30,:inst)
            ON CONFLICT DO NOTHING
        """), {"id": str(uuid.uuid4()), "jid": jid,
               "inst": f"File SR-22 with {state['name']} DMV within 30 days of court order. Maintain for 3 years. Notify DMV immediately on cancellation."})

    # FR-44 form (VA and FL)
    if state["fr44"]:
        db.execute(text("""
            INSERT INTO jurisdiction_forms (id, jurisdiction_id, lob, form_code, form_name, trigger_condition, filing_entity, deadline_days, instructions)
            VALUES (:id,:jid,'auto','FR-44','FR-44 Certificate of Financial Responsibility (DUI)',
                    'on_dui_conviction','DMV',30,:inst)
            ON CONFLICT DO NOTHING
        """), {"id": str(uuid.uuid4()), "jid": jid,
               "inst": f"FR-44 required in {state['name']} for DUI/DWI convictions. Higher limits required than SR-22. Maintain for 3 years."})

    db.commit()
    return jid

# ── Routes ────────────────────────────────────────────────────────────────────

def _seed_country(country: dict, db: Session) -> str:
    """Insert one international country into jurisdictions."""
    existing = db.execute(text(
        "SELECT id FROM jurisdictions WHERE country_code=:cc AND state_code IS NULL"
    ), {"cc": country["code"]}).fetchone()

    config = {
        "region":            country.get("region", ""),
        "currency":          country.get("currency", ""),
        "regulator":         country.get("regulator", ""),
        "tp_required":       country.get("tp_required", True),
        "no_fault":          country.get("no_fault", False),
        "cancellation_days": country.get("cancellation_days", 30),
        "motor_notes":       country.get("motor_notes", ""),
        "key_rules":         country.get("key_rules", ""),
        "lobs_available":    country.get("lobs", []),
    }

    if existing:
        jid = dict(existing._mapping)["id"]
        db.execute(text("""
            UPDATE jurisdictions SET config=CAST(:cfg AS jsonb), updated_at=NOW()
            WHERE id=:id
        """), {"cfg": json.dumps(config), "id": jid})
    else:
        jid = str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO jurisdictions (id, country_code, country_name, state_code, state_name, config)
            VALUES (:id, :cc, :cn, NULL, NULL, CAST(:cfg AS jsonb))
        """), {"id": jid, "cc": country["code"], "cn": country["name"],
               "cfg": json.dumps(config)})

    # Seed LOBs for the country
    for lob_name in country.get("lobs", []):
        lob_key = lob_name.split(" ")[0].lower().replace("/","_").replace("(","").replace(")","").replace("&","")[:30]
        db.execute(text("""
            INSERT INTO jurisdiction_products (id, jurisdiction_id, lob, available, required, min_limits, required_coverages)
            VALUES (:id, :jid, :lob, TRUE,
                    CASE WHEN :lob IN ('motor','auto','ctp','kfz','soat','rc_auto','tp') THEN TRUE ELSE FALSE END,
                    CAST('{}' AS jsonb), CAST('[]' AS jsonb))
            ON CONFLICT (jurisdiction_id, lob) DO NOTHING
        """), {"id": str(uuid.uuid4()), "jid": jid, "lob": lob_key})

    db.commit()
    return jid

@router.post("/seed")
def seed_jurisdictions(db: Session = Depends(get_db)):
    """Populate all 50 US states + DC with real insurance requirements."""
    ensure_tables(db)
    seeded = []
    for state in US_STATES:
        jid = _seed_state(state, db)
        seeded.append({"state": state["code"], "id": jid})
    return {"seeded": len(seeded), "jurisdictions": seeded}

@router.post("/seed-international")
def seed_international(db: Session = Depends(get_db)):
    """Populate 20+ international countries with insurance market data."""
    ensure_tables(db)
    seeded = []
    for country in COUNTRIES_INTL:
        jid = _seed_country(country, db)
        seeded.append({"country": country["code"], "name": country["name"], "id": jid})
    return {"seeded": len(seeded), "countries": seeded}

@router.get("")
def list_jurisdictions(country: str = "US", db: Session = Depends(get_db)):
    ensure_tables(db)
    rows = db.execute(text("""
        SELECT j.*, COUNT(jp.id) as product_count
        FROM jurisdictions j
        LEFT JOIN jurisdiction_products jp ON jp.jurisdiction_id = j.id
        WHERE j.country_code = :cc
        GROUP BY j.id
        ORDER BY j.state_name
    """), {"cc": country}).fetchall()
    return [r2d(r) for r in rows]

@router.get("/lookup")
def lookup_jurisdiction(country: str = "US", state: str = None, city: str = None, db: Session = Depends(get_db)):
    """Get jurisdiction config for a specific location — most specific match wins."""
    ensure_tables(db)
    # City-level → state-level → country-level
    for (cc, sc, ct) in [(country, state, city), (country, state, None), (country, None, None)]:
        q = "SELECT * FROM jurisdictions WHERE country_code=:cc"
        p: dict = {"cc": cc}
        if sc: q += " AND state_code=:sc"; p["sc"] = sc
        else:  q += " AND state_code IS NULL"
        if ct: q += " AND city=:city"; p["city"] = ct
        else:  q += " AND city IS NULL"
        row = db.execute(text(q), p).fetchone()
        if row:
            j = r2d(row)
            j["products"] = _get_products(j["id"], db)
            j["forms"]    = _get_forms(j["id"], db)
            return j
    return {"error": "jurisdiction not found", "country": country, "state": state}

def _get_products(jid: str, db: Session):
    rows = db.execute(text("SELECT * FROM jurisdiction_products WHERE jurisdiction_id=:id ORDER BY lob"), {"id": jid}).fetchall()
    return [r2d(r) for r in rows]

def _get_forms(jid: str, db: Session):
    rows = db.execute(text("SELECT * FROM jurisdiction_forms WHERE jurisdiction_id=:id ORDER BY form_code"), {"id": jid}).fetchall()
    return [r2d(r) for r in rows]

@router.get("/lob-fields")
def get_lob_fields(lob: str, db: Session = Depends(get_db)):
    """Return field config for a line of business (what fields are required/optional)."""
    return LOB_FIELD_CONFIG.get(lob, {"always": [], "optional": []})

@router.get("/lobs")
def get_lobs():
    return {"lobs": LOB_GLOBAL}

@router.get("/{jid}")
def get_jurisdiction(jid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    row = db.execute(text("SELECT * FROM jurisdictions WHERE id=:id"), {"id": jid}).fetchone()
    if not row: raise HTTPException(404, "Not found")
    j = r2d(row)
    j["products"] = _get_products(jid, db)
    j["forms"]    = _get_forms(jid, db)
    return j

@router.get("/{jid}/products")
def get_products(jid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    return _get_products(jid, db)

@router.get("/{jid}/forms")
def get_forms(jid: str, db: Session = Depends(get_db)):
    ensure_tables(db)
    return _get_forms(jid, db)

@router.put("/{jid}/products/{lob}")
def upsert_product(jid: str, lob: str, body: dict, db: Session = Depends(get_db)):
    """Agency-level override of a product's rules for a jurisdiction."""
    ensure_tables(db)
    existing = db.execute(text("SELECT id FROM jurisdiction_products WHERE jurisdiction_id=:jid AND lob=:lob"),
                          {"jid": jid, "lob": lob}).fetchone()
    if existing:
        db.execute(text("""
            UPDATE jurisdiction_products
            SET available=:av, required=:req, min_limits=CAST(:ml AS jsonb),
                required_coverages=CAST(:rc AS jsonb), required_endorsements=CAST(:re AS jsonb),
                agency_notes=:notes, updated_at=NOW()
            WHERE jurisdiction_id=:jid AND lob=:lob
        """), {"jid": jid, "lob": lob, "av": body.get("available", True),
               "req": body.get("required", False),
               "ml": json.dumps(body.get("min_limits", {})),
               "rc": json.dumps(body.get("required_coverages", [])),
               "re": json.dumps(body.get("required_endorsements", [])),
               "notes": body.get("agency_notes")})
    else:
        db.execute(text("""
            INSERT INTO jurisdiction_products (id,jurisdiction_id,lob,available,required,min_limits,required_coverages,required_endorsements,agency_notes)
            VALUES (:id,:jid,:lob,:av,:req,CAST(:ml AS jsonb),CAST(:rc AS jsonb),CAST(:re AS jsonb),:notes)
        """), {"id": str(uuid.uuid4()), "jid": jid, "lob": lob,
               "av": body.get("available", True), "req": body.get("required", False),
               "ml": json.dumps(body.get("min_limits", {})),
               "rc": json.dumps(body.get("required_coverages", [])),
               "re": json.dumps(body.get("required_endorsements", [])),
               "notes": body.get("agency_notes")})
    db.commit()
    return {"ok": True}

@router.post("/validate-policy")
def validate_policy(body: dict, db: Session = Depends(get_db)):
    """
    Check a policy's limits against jurisdiction minimums.
    Input: {country, state, lob, limits: {bi_per_person_k, bi_per_accident_k, pd_k, pip_k, ...}}
    Returns: {valid, violations: [{field, required, provided, message}]}
    """
    ensure_tables(db)
    country = body.get("country", "US")
    state   = body.get("state")
    lob     = body.get("lob", "auto")
    limits  = body.get("limits", {})

    j = lookup_jurisdiction(country, state, db=db)
    if "error" in j:
        return {"valid": True, "violations": [], "warning": "Jurisdiction not found, no validation performed"}

    product = next((p for p in j.get("products", []) if p["lob"] == lob), None)
    if not product:
        return {"valid": True, "violations": [], "warning": f"No rules configured for {lob} in {state}"}

    min_lim  = product.get("min_limits") or {}
    req_covs = product.get("required_coverages") or []
    violations = []

    field_map = {
        "bi_per_person_k":   ("Bodily Injury per Person",   "bi_per_person"),
        "bi_per_accident_k": ("Bodily Injury per Accident", "bi_per_accident"),
        "property_damage_k": ("Property Damage",            "pd"),
        "pip_k":             ("PIP / No-Fault",             "pip"),
        "um_per_person_k":   ("Uninsured Motorist per Person", "um_per_person"),
        "um_per_accident_k": ("Uninsured Motorist per Accident","um_per_accident"),
    }

    for key, (label, cov_key) in field_map.items():
        required_val = min_lim.get(key)
        if required_val is None:
            continue
        provided_val = limits.get(key) or limits.get(cov_key)
        if provided_val is None:
            if cov_key in req_covs or key.replace("_k","") in req_covs:
                violations.append({
                    "field": key, "label": label,
                    "required_k": required_val, "provided_k": None,
                    "message": f"{label} is required in {state} (minimum ${required_val}k) but was not provided."
                })
        elif float(provided_val) < float(required_val):
            violations.append({
                "field": key, "label": label,
                "required_k": required_val, "provided_k": provided_val,
                "message": f"{label} is below {state} minimum: provided ${provided_val}k, required ${required_val}k."
            })

    cfg = j.get("config") or {}
    if cfg.get("pip_required") and not limits.get("pip_k") and not limits.get("pip"):
        violations.append({
            "field": "pip_k", "label": "PIP / No-Fault",
            "required_k": min_lim.get("pip_k"), "provided_k": None,
            "message": f"PIP is mandatory in {state} (no-fault state). Must be included."
        })

    return {
        "valid": len(violations) == 0,
        "violations": violations,
        "jurisdiction": {"country": country, "state": state, "config": cfg},
        "minimum_limits": min_lim,
    }
