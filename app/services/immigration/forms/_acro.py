"""
Shared AcroForm engine for the USCIS auto-fill modules (I-539, I-765).

A form module declares:
  FORM, FORM_PDF, EDITION
  TEXT_FIELDS  key → (AcroForm field name, label, group); drop-downs listed in COMBOS
  CHOICES      key → {"label", "group", "required", "options": {value: (field-name prefix, on-state)}}
               (a radio-style group of checkboxes; value is what staff enter)
  MIRRORS      optional: key → regex of extra field names that repeat the same value (e.g. A-Number page headers)
  REQUIRED     text keys that must be filled
  gather(case, intake, form_data) → key → {value, source}

Never filled: signatures, signature dates, barcodes. Nothing is invented — a value
that isn't in any source stays blank and is reported as missing.
"""
import re
from typing import Dict, Optional, Tuple

NEVER = re.compile(r"Signature|DateofSignature|DateOfSignature|BarCode", re.I)


def mdy(v) -> Optional[str]:
    if not v:
        return None
    s = str(v).strip()
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(2)):02d}/{int(m.group(3)):02d}/{m.group(1)}"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}" if m else s


_US_ADDR = re.compile(r"^\s*(?P<street>[^,\n]+?)\s*(?:,\s*(?:apt|ste|suite|unit|#)\.?\s*(?P<unit>[\w-]+))?\s*[,\n]\s*"
                      r"(?P<city>[A-Za-z .'-]+?)\s*,\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})(?:-\d{4})?\s*$", re.I)


def addr(text: Optional[str]) -> Dict[str, str]:
    m = _US_ADDR.match((text or "").strip())
    return {k: v.strip() for k, v in m.groupdict().items() if v} if m else {}


class Collector(dict):
    """key → {value, source}; the first source to supply a key wins."""

    def put(self, key, value, source):
        if value not in (None, "") and key not in self:
            self[key] = {"value": str(value).strip(), "source": source}

    def name(self, prefix: str, full: Optional[str], source="derived"):
        parts = (full or "").split()
        if len(parts) >= 2 and f"{prefix}_family" not in self:
            self.put(f"{prefix}_given", parts[0], source)
            self.put(f"{prefix}_family", parts[-1], source)
            if len(parts) > 2:
                self.put(f"{prefix}_middle", " ".join(parts[1:-1]), source)


def editable_keys(mod) -> set:
    return set(mod.TEXT_FIELDS) | set(getattr(mod, "CHOICES", {}))


def edition_ok(mod) -> bool:
    import fitz
    with fitz.open(mod.FORM_PDF) as d:
        text = " ".join(d[0].get_text().split()) + " " + " ".join(d[len(d) - 1].get_text().split())
    return f"Edition {mod.EDITION}" in text


def preview(mod, case: Dict, intake: Dict, form_data: Dict) -> Dict:
    vals = mod.gather(case, intake, form_data)
    blank = {"value": "", "source": None}
    rows = [{"key": k, "label": lab, "group": grp, "required": k in mod.REQUIRED, **vals.get(k, blank)}
            for k, (_, lab, grp) in mod.TEXT_FIELDS.items()]
    rows += [{"key": k, "label": f"{c['label']} ({' / '.join(c['options'])})", "group": c["group"],
              "required": c.get("required", False), "options": list(c["options"]), **vals.get(k, blank)}
             for k, c in getattr(mod, "CHOICES", {}).items()]
    return {"form": mod.FORM, "edition": mod.EDITION, "edition_ok": edition_ok(mod), "fields": rows,
            "filled": sum(1 for r in rows if r["value"]), "total": len(rows),
            "missing_required": [r["label"] for r in rows if r["required"] and not r["value"]],
            "derived": [r["label"] for r in rows if r["source"] == "derived"]}


def set_choice(doc, w, code: str) -> bool:
    """Select a drop-down option by its export code (PyMuPDF can't update lists whose code and label differ)."""
    import fitz
    codes = {(c[0] if isinstance(c, (list, tuple)) else c).strip() for c in w.choice_values}
    if code not in codes:
        return False
    doc.xref_set_key(w.xref, "V", fitz.get_pdf_str(code))
    doc.xref_set_key(w.xref, "AP", "null")          # drop the stale appearance; viewers redraw it
    return True


def fill(mod, case: Dict, intake: Dict, form_data: Dict) -> Tuple[bytes, Dict]:
    import fitz
    vals = mod.gather(case, intake, form_data)
    report = preview(mod, case, intake, form_data)
    targets = {mod.TEXT_FIELDS[k][0]: (k, v["value"]) for k, v in vals.items() if k in mod.TEXT_FIELDS}
    mirrors = [(re.compile(rx), vals[k]["value"], k) for k, rx in getattr(mod, "MIRRORS", {}).items() if k in vals]
    checks, skipped = {}, []
    for k, c in getattr(mod, "CHOICES", {}).items():
        v = vals.get(k, {}).get("value")
        if not v:
            continue
        opt = c["options"].get(v) or next((o for lab, o in c["options"].items() if lab.lower() == v.lower()), None)
        if opt:
            checks[opt] = k
        else:
            skipped.append(f"{c['label']}: “{v}” isn't one of {', '.join(c['options'])}")
    doc = fitz.open(mod.FORM_PDF)
    for page in doc:
        for w in page.widgets():
            name = w.field_name
            if NEVER.search(name):
                continue
            try:
                mirror = next(((k, v) for rx, v, k in mirrors if rx.search(name)), None) if name not in targets else None
                if name in targets or mirror:
                    key, value = targets.get(name) or mirror
                    if w.field_type_string == "ComboBox":
                        code = mod.COMBO_CODE.get(key, str.upper)(value) if hasattr(mod, "COMBO_CODE") else value.upper()
                        if not set_choice(doc, w, code):
                            skipped.append(f"{mod.TEXT_FIELDS[key][1]}: “{value}” is not one of the form's options")
                        continue
                    if w.text_maxlen and len(value) > w.text_maxlen and re.search(r"phone|mobile", key):
                        value = re.sub(r"\D", "", value)[-10:]          # "(916) 555-0100" → "9165550100"
                    if w.text_maxlen and len(value) > w.text_maxlen:
                        skipped.append(f"{mod.TEXT_FIELDS[key][1]}: longer than the form allows ({w.text_maxlen})")
                        continue
                    w.field_value = value
                    w.update()
                elif w.field_type_string == "CheckBox" and (name.rsplit("[", 1)[0], w.on_state()) in checks:
                    w.field_value = w.on_state()
                    w.update()
            except Exception as e:                      # one bad field must not lose the rest
                skipped.append(f"{name}: {e}")
    cat = doc.pdf_catalog()
    af = doc.xref_get_key(cat, "AcroForm")
    if af[0] == "xref":
        doc.xref_set_key(int(af[1].split()[0]), "NeedAppearances", "true")
    elif af[0] == "dict":
        doc.xref_set_key(cat, "AcroForm/NeedAppearances", "true")
    report["skipped"] = skipped
    return doc.tobytes(garbage=3, deflate=True), report
