"""
ReAct Agent — Reason + Act loop powered by phi3:mini (Ollama).

Loop:
  1. Build prompt with history + available tools
  2. Ask phi3:mini → expect JSON: {thought, tool, args} OR {thought, final_answer}
  3. Execute tool, append observation to history
  4. Repeat until final_answer or max_iterations
  5. If tool == create_approval_request → pause, set status = waiting_approval
  6. On resume (human approved) → continue from saved history
"""
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import ollama
from sqlalchemy import text

from app.core.database import SessionLocal
from app.services.agent_tools import TOOL_DESCRIPTIONS, call_tool

logger = logging.getLogger(__name__)

OLLAMA_MODEL = "phi3:mini"
MAX_ITERATIONS = 6

SYSTEM_PROMPT = """You are a JSON-only ReAct agent. Output ONLY a single JSON object. No markdown, no backticks, no explanation text.

{tool_descriptions}

Output format — pick ONE:

Call a tool:
{{"thought":"brief reason","tool":"tool_name","args":{{}}}}

Finish the task:
{{"thought":"brief reason","tool":"final_answer","args":{{"summary":"what was done","actions_taken":[]}}}}

Ask human approval (for: {approval_level}):
{{"thought":"need approval","tool":"create_approval_request","args":{{"run_id":"{run_id}","title":"...","description":"...","proposed_action":{{}},"reasoning":"..."}}}}

Rules: valid JSON only. No prose. No markdown fences. Start your response with {{ and end with }}.
"""


def _save_history(db, run_id: str, history: List, status: str, iteration: int):
    db.execute(text("""
        UPDATE agent_runs
        SET history = CAST(:h AS jsonb), status = :s, iteration = :i, updated_at = NOW()
        WHERE id = :id
    """), {"h": json.dumps(history), "s": status, "i": iteration, "id": run_id})
    db.commit()


def _log_tool_call(db, run_id: str, iteration: int, tool: str, args: Dict, result: Any, latency_ms: int, error: str = None):
    db.execute(text("""
        INSERT INTO agent_tool_calls (id, run_id, iteration, tool_name, args, result, error, latency_ms)
        VALUES (:id, :run_id, :iter, :tool,
                CAST(:args AS jsonb), CAST(:result AS jsonb), :error, :lat)
    """), {
        "id": str(uuid.uuid4()), "run_id": run_id, "iter": iteration,
        "tool": tool, "args": json.dumps(args),
        "result": json.dumps(result) if result is not None else None,
        "error": error, "lat": latency_ms
    })
    db.commit()


def _parse_llm_json(raw: str) -> Optional[Dict]:
    """Extract and normalize JSON from phi3 output.
    phi3 sometimes uses {action:{toolName:...}, input:{...}} instead of {tool:..., args:{...}}.
    """
    raw = raw.strip()
    # Strip qwen3 <think>...</think> reasoning blocks before parsing
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    raw = re.sub(r"```[a-z]*\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)

    # find outermost { ... }
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    parsed = None
    for i, ch in enumerate(raw[start:], start):
        if ch == "{": depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(raw[start:i+1])
                except json.JSONDecodeError:
                    return None
                break
    if not parsed:
        return None

    # Normalize phi3 variants → standard {thought, tool, args}
    if "tool" not in parsed:
        # phi3 uses action.toolName or action.tool
        action = parsed.get("action") or {}
        tool = action.get("toolName") or action.get("tool") or parsed.get("toolName") or ""
        args = parsed.get("input") or parsed.get("parameters") or parsed.get("args") or {}
        if tool:
            parsed["tool"] = tool
            parsed["args"] = args

    return parsed


def run_agent(run_id: str):
    """
    Main ReAct loop. Called as a background task.
    Reads run from DB, executes loop, writes state back after each iteration.
    """
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT * FROM agent_runs WHERE id = :id"), {"id": run_id}).fetchone()
        if not row:
            logger.error(f"Agent run {run_id} not found")
            return
        run = dict(row._mapping)

        history: List[Dict] = run.get("history") or []
        iteration: int = run.get("iteration") or 0
        approval_level: str = run.get("approval_level") or "external"

        # Build initial user message if history is empty
        if not history:
            trigger = run.get("trigger_payload") or {}
            instruction = run.get("instruction") or ""
            task = run.get("task_name") or "general task"
            sector = run.get("sector") or "general"
            history.append({
                "role": "user",
                "content": (
                    f"Sector: {sector}\n"
                    f"Task: {task}\n"
                    f"Instruction: {instruction}\n"
                    f"Context: {json.dumps(trigger)}\n\n"
                    f"Start working on this task. Gather information using tools, then decide what to do."
                )
            })

        system = SYSTEM_PROMPT.format(
            tool_descriptions=TOOL_DESCRIPTIONS,
            run_id=run_id,
            approval_level=approval_level
        )

        client = ollama.Client()

        while iteration < MAX_ITERATIONS:
            iteration += 1
            _save_history(db, run_id, history, "thinking", iteration)
            logger.info(f"[Agent {run_id}] iteration {iteration}")

            # Build messages for Ollama
            messages = [{"role": "system", "content": system}] + history

            try:
                t0 = datetime.utcnow()
                response = client.chat(
                    model=OLLAMA_MODEL,
                    messages=messages,
                    options={"temperature": 0.1, "num_predict": 512, "think": False}
                )
                latency = int((datetime.utcnow() - t0).total_seconds() * 1000)
                raw_content = response["message"]["content"]
            except Exception as e:
                logger.error(f"[Agent {run_id}] Ollama error: {e}")
                _save_history(db, run_id, history, "failed", iteration)
                db.execute(text("UPDATE agent_runs SET error_message=:e, ended_at=NOW() WHERE id=:id"),
                           {"e": str(e), "id": run_id})
                db.commit()
                return

            # Append LLM response to history
            history.append({"role": "assistant", "content": raw_content})

            # Parse JSON output
            parsed = _parse_llm_json(raw_content)
            if not parsed:
                logger.warning(f"[Agent {run_id}] Could not parse JSON: {raw_content[:200]}")
                history.append({"role": "user", "content": "Your response was not valid JSON. Please respond with ONLY a JSON object."})
                continue

            thought = parsed.get("thought", "")
            tool_name = parsed.get("tool", "")
            args = parsed.get("args", {})

            logger.info(f"[Agent {run_id}] thought='{thought[:80]}' tool={tool_name}")

            if not tool_name:
                history.append({"role": "user", "content": "Missing 'tool' field. Please include a tool name."})
                continue

            # Execute tool
            _save_history(db, run_id, history, "acting", iteration)
            t0 = datetime.utcnow()
            result = call_tool(tool_name, args)
            latency = int((datetime.utcnow() - t0).total_seconds() * 1000)
            _log_tool_call(db, run_id, iteration, tool_name, args, result, latency)

            # Handle final_answer
            if tool_name == "final_answer" or (isinstance(result, dict) and result.get("__final__")):
                _save_history(db, run_id, history, "completed", iteration)
                db.execute(text("""
                    UPDATE agent_runs SET status='completed', outcome=CAST(:o AS jsonb), ended_at=NOW()
                    WHERE id=:id
                """), {"o": json.dumps(result), "id": run_id})
                db.commit()
                logger.info(f"[Agent {run_id}] completed after {iteration} iterations")
                return

            # Handle approval escalation
            if tool_name == "create_approval_request":
                # Tool already updated DB status to waiting_approval
                history.append({"role": "user", "content": f"Approval request created: {json.dumps(result)}. Pausing until human decision."})
                _save_history(db, run_id, history, "waiting_approval", iteration)
                logger.info(f"[Agent {run_id}] paused — waiting for approval {result.get('approval_id')}")
                return  # will be resumed via resume_agent()

            # Normal tool result — append as observation
            history.append({
                "role": "user",
                "content": f"Tool result for {tool_name}: {json.dumps(result)}"
            })

        # Max iterations hit
        logger.warning(f"[Agent {run_id}] max iterations reached")
        _save_history(db, run_id, history, "failed", iteration)
        db.execute(text("""
            UPDATE agent_runs SET status='failed', error_message='Max iterations reached', ended_at=NOW()
            WHERE id=:id
        """), {"id": run_id})
        db.commit()

    finally:
        db.close()


def resume_agent(run_id: str, decision: str, comment: str = ""):
    """
    Resume a paused agent after human approval/rejection.
    decision: 'approve' | 'reject'
    """
    db = SessionLocal()
    try:
        row = db.execute(text("SELECT * FROM agent_runs WHERE id=:id"), {"id": run_id}).fetchone()
        if not row:
            return {"error": "Run not found"}
        run = dict(row._mapping)

        if run["status"] != "waiting_approval":
            return {"error": f"Run is not paused (status={run['status']})"}

        history: List = run.get("history") or []

        if decision == "reject":
            history.append({"role": "user", "content": f"Human rejected the proposed action. Reason: {comment or 'No reason given'}. Please reconsider and either try a different approach or call final_answer."})
            db.execute(text("UPDATE agent_runs SET status='thinking', updated_at=NOW() WHERE id=:id"), {"id": run_id})
            db.commit()
            db.close()
            # Continue loop with rejection context
            run_agent(run_id)
        else:
            history.append({"role": "user", "content": f"Human approved the action. {comment or ''} Proceed to execute and then call final_answer."})
            db.execute(text("UPDATE agent_runs SET history=CAST(:h AS jsonb), status='thinking', updated_at=NOW() WHERE id=:id"),
                       {"h": json.dumps(history), "id": run_id})
            db.commit()
            db.close()
            run_agent(run_id)

        return {"ok": True, "decision": decision}
    except Exception:
        db.close()
        raise
