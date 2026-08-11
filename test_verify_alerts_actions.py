"""
Verification Script: Alerts & Action Plans
==========================================
Checks all documents in the database for:
1. Presence of 'alerts' and 'action_plan' keys
2. Correct structure/schema of each alert and action item
3. UI config integration (alerts_config, action tables/sections)
4. Reports overall compliance statistics
"""
import os
import sys
import json

sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from app.models.client import Client
from app.models.policy import Policy
from app.models.document import Document
from app.core.database import SessionLocal


def validate_alert(alert: dict) -> list:
    """Validate a single alert dict and return list of issues."""
    issues = []
    if not isinstance(alert, dict):
        return [f"Alert is not a dict: {type(alert)}"]

    # Required keys
    if "type" not in alert:
        issues.append("Missing 'type' key")
    elif alert["type"] not in ("error", "warning", "info"):
        issues.append(f"Invalid type: '{alert['type']}' (expected error/warning/info)")

    if "message" not in alert:
        issues.append("Missing 'message' key")
    elif not isinstance(alert["message"], str) or not alert["message"].strip():
        issues.append("Empty or non-string 'message'")

    if "title" not in alert:
        issues.append("Missing 'title' key")

    return issues


def validate_action(action: dict) -> list:
    """Validate a single action plan item and return list of issues."""
    issues = []
    if not isinstance(action, dict):
        return [f"Action is not a dict: {type(action)}"]

    if "priority" not in action:
        issues.append("Missing 'priority' key")
    elif action["priority"] not in ("high", "medium", "low", "immediate", "short_term", "long_term"):
        issues.append(f"Invalid priority: '{action['priority']}'")

    if "action" not in action:
        issues.append("Missing 'action' key")
    elif not isinstance(action["action"], str) or not action["action"].strip():
        issues.append("Empty or non-string 'action'")

    if "detail" not in action:
        issues.append("Missing 'detail' key")

    return issues


def check_ui_config(ui_config: dict) -> dict:
    """Check UI config for alerts and action plan integration."""
    result = {
        "has_alerts_config": False,
        "has_action_table": False,
        "has_action_section": False,
    }
    if not isinstance(ui_config, dict):
        return result

    result["has_alerts_config"] = "alerts_config" in ui_config

    tables = ui_config.get("tables", [])
    if isinstance(tables, list):
        result["has_action_table"] = any(
            t.get("id") in ("action_plan", "actions") for t in tables if isinstance(t, dict)
        )

    sections = ui_config.get("sections", [])
    if isinstance(sections, list):
        result["has_action_section"] = any(
            s.get("id") in ("action_plan", "actions") for s in sections if isinstance(s, dict)
        )

    return result


def main():
    db = SessionLocal()
    try:
        docs = db.query(Document).all()
        total = len(docs)
        print(f"\n{'='*120}")
        print(f"  ALERTS & ACTION PLAN VERIFICATION REPORT")
        print(f"  Total documents in database: {total}")
        print(f"{'='*120}\n")

        # Statistics
        stats = {
            "total": total,
            "has_alerts": 0,
            "has_action_plan": 0,
            "has_both": 0,
            "has_neither": 0,
            "alerts_valid": 0,
            "alerts_invalid": 0,
            "actions_valid": 0,
            "actions_invalid": 0,
            "ui_alerts_config": 0,
            "ui_action_table": 0,
            "ui_action_section": 0,
            "has_advanced_analytics": 0,
        }
        
        # Per-domain stats
        domain_stats = {}

        for doc in docs:
            meta = doc.doc_metadata or {}
            analyzed_data = meta.get("analyzed_data") or {}
            doc_type = doc.doc_type or "unknown"
            
            # Initialize domain stats
            if doc_type not in domain_stats:
                domain_stats[doc_type] = {"total": 0, "has_alerts": 0, "has_actions": 0, "has_both": 0}
            domain_stats[doc_type]["total"] += 1
            
            alerts = analyzed_data.get("alerts", [])
            action_plan = analyzed_data.get("action_plan", [])
            ui_config = analyzed_data.get("ui_config", {})
            has_adv = analyzed_data.get("has_advanced_analytics", False)

            has_alerts = isinstance(alerts, list) and len(alerts) > 0
            has_actions = isinstance(action_plan, list) and len(action_plan) > 0

            if has_alerts:
                stats["has_alerts"] += 1
                domain_stats[doc_type]["has_alerts"] += 1
            if has_actions:
                stats["has_action_plan"] += 1
                domain_stats[doc_type]["has_actions"] += 1
            if has_alerts and has_actions:
                stats["has_both"] += 1
                domain_stats[doc_type]["has_both"] += 1
            if not has_alerts and not has_actions:
                stats["has_neither"] += 1
            if has_adv:
                stats["has_advanced_analytics"] += 1

            # Validate alert structures
            all_alerts_valid = True
            alert_issues = []
            if has_alerts:
                for i, alert in enumerate(alerts):
                    issues = validate_alert(alert)
                    if issues:
                        all_alerts_valid = False
                        alert_issues.append((i, issues))
                if all_alerts_valid:
                    stats["alerts_valid"] += 1
                else:
                    stats["alerts_invalid"] += 1

            # Validate action plan structures
            all_actions_valid = True
            action_issues = []
            if has_actions:
                for i, action in enumerate(action_plan):
                    issues = validate_action(action)
                    if issues:
                        all_actions_valid = False
                        action_issues.append((i, issues))
                if all_actions_valid:
                    stats["actions_valid"] += 1
                else:
                    stats["actions_invalid"] += 1

            # Check UI config
            ui_check = check_ui_config(ui_config)
            if ui_check["has_alerts_config"]:
                stats["ui_alerts_config"] += 1
            if ui_check["has_action_table"]:
                stats["ui_action_table"] += 1
            if ui_check["has_action_section"]:
                stats["ui_action_section"] += 1

            # Report per-document
            status_icon = "✅" if (has_alerts and has_actions) else "⚠️" if (has_alerts or has_actions) else "❌"
            print(f"{status_icon} {doc.filename[:45]:<45} | Type: {doc_type:<22} | Alerts: {len(alerts):<3} | Actions: {len(action_plan):<3} | UI: A={ui_check['has_alerts_config']} T={ui_check['has_action_table']} S={ui_check['has_action_section']}")

            # Print any schema issues
            if alert_issues:
                for idx, issues in alert_issues:
                    print(f"     ⛔ Alert[{idx}] issues: {', '.join(issues)}")
            if action_issues:
                for idx, issues in action_issues:
                    print(f"     ⛔ Action[{idx}] issues: {', '.join(issues)}")

        # Summary
        print(f"\n{'='*120}")
        print(f"  SUMMARY")
        print(f"{'='*120}")
        print(f"  Documents with alerts:       {stats['has_alerts']:>3}/{total} ({100*stats['has_alerts']/total:.1f}%)")
        print(f"  Documents with action plan:  {stats['has_action_plan']:>3}/{total} ({100*stats['has_action_plan']/total:.1f}%)")
        print(f"  Documents with BOTH:         {stats['has_both']:>3}/{total} ({100*stats['has_both']/total:.1f}%)")
        print(f"  Documents with NEITHER:      {stats['has_neither']:>3}/{total} ({100*stats['has_neither']/total:.1f}%)")
        print(f"  has_advanced_analytics=True:  {stats['has_advanced_analytics']:>3}/{total}")
        print(f"")
        print(f"  Alerts schema valid:          {stats['alerts_valid']:>3}/{stats['has_alerts']} docs with alerts")
        print(f"  Alerts schema invalid:        {stats['alerts_invalid']:>3}/{stats['has_alerts']} docs with alerts")
        print(f"  Actions schema valid:         {stats['actions_valid']:>3}/{stats['has_action_plan']} docs with actions")
        print(f"  Actions schema invalid:       {stats['actions_invalid']:>3}/{stats['has_action_plan']} docs with actions")
        print(f"")
        print(f"  UI: alerts_config present:    {stats['ui_alerts_config']:>3}/{total}")
        print(f"  UI: action table present:     {stats['ui_action_table']:>3}/{total}")
        print(f"  UI: action section present:   {stats['ui_action_section']:>3}/{total}")

        # Per-domain breakdown
        print(f"\n{'='*120}")
        print(f"  PER-DOMAIN BREAKDOWN")
        print(f"{'='*120}")
        print(f"  {'Domain':<25} | {'Total':>5} | {'Alerts':>6} | {'Actions':>7} | {'Both':>4}")
        print(f"  {'-'*25}-+-{'-'*5}-+-{'-'*6}-+-{'-'*7}-+-{'-'*4}")
        for domain, ds in sorted(domain_stats.items()):
            print(f"  {domain:<25} | {ds['total']:>5} | {ds['has_alerts']:>6} | {ds['has_actions']:>7} | {ds['has_both']:>4}")

        # Identify documents that need reprocessing
        needs_reprocess = []
        for doc in docs:
            meta = doc.doc_metadata or {}
            analyzed_data = meta.get("analyzed_data") or {}
            alerts = analyzed_data.get("alerts", [])
            action_plan = analyzed_data.get("action_plan", [])
            if not alerts and not action_plan:
                needs_reprocess.append((str(doc.id), doc.filename, doc.doc_type))

        if needs_reprocess:
            print(f"\n{'='*120}")
            print(f"  DOCUMENTS NEEDING REPROCESSING ({len(needs_reprocess)} docs)")
            print(f"{'='*120}")
            for doc_id, filename, dtype in needs_reprocess:
                print(f"  {doc_id} | {filename[:45]:<45} | {dtype}")

        print(f"\n{'='*120}\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
