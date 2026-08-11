"""
Base agent class — all domain agents inherit from this.

Each agent has:
  - system_prompt  : domain-specific instructions for the LLM
  - tools          : dict of tool_name → callable
  - model          : which local Ollama model to use
  - run()          : ReAct loop (reason → act → observe → repeat)
"""
import inspect
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

import ollama

from app.core import config as app_config
from app.core.database import SessionLocal
from app.core.tenant import get_current_tenant_id, apply_tenant_to_db

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6


class AgentResult:
    def __init__(
        self,
        answer: str,
        sources: List[str],
        steps: List[Dict],
        chart_data: Optional[Dict] = None,
        domain: str = "general",
    ):
        self.answer = answer
        self.sources = sources
        self.steps = steps
        self.chart_data = chart_data
        self.domain = domain

    def to_dict(self) -> Dict:
        return {
            "answer":     self.answer,
            "sources":    self.sources,
            "steps":      self.steps,
            "chart_data": self.chart_data,
            "_domain":    self.domain,
        }


class BaseAgent:
    """
    Base ReAct agent. Domain agents override:
      - system_prompt  → what the LLM knows about itself
      - register_tools()  → which tools it can call
      - model          → override to use a different local model
    """

    domain: str = "general"
    model: str = None                   # None = use app default (config.model)

    system_prompt: str = """You are a document analysis agent.
Answer questions based only on the documents provided.
When you need information, call the appropriate tool.
Return JSON with: {"thought": "...", "tool": "tool_name", "args": {...}}
When you have enough information to answer, return:
{"thought": "...", "tool": "finish", "args": {"answer": "..."}}
"""

    def __init__(self, db=None):
        self._db = db or SessionLocal()
        self._own_db = db is None
        self._tenant_id = get_current_tenant_id()
        # Set RLS session variable so all queries through this session are scoped
        apply_tenant_to_db(self._db, self._tenant_id)
        self._tools: Dict[str, Callable] = {}
        self.register_tools()

    @property
    def _tenant_filter(self) -> str:
        """SQL fragment: AND tenant_id = '<uuid>' — use in every WHERE clause."""
        return f"AND tenant_id = '{self._tenant_id}'"

    @property
    def _tenant_params(self) -> dict:
        """Named param for parameterized queries: {'tenant_id': '<uuid>'}"""
        return {"tenant_id": self._tenant_id}

    def register_tools(self):
        """Override in subclasses to register domain-specific tools."""
        pass

    def _tool(self, name: str, fn: Callable):
        self._tools[name] = fn

    def _get_model(self) -> str:
        return self.model or app_config.model

    def _tool_descriptions(self) -> str:
        lines = []
        for name, fn in self._tools.items():
            doc = (fn.__doc__ or "").strip().split("\n")[0]
            lines.append(f"  - {name}: {doc}")
        return "\n".join(lines) if lines else "  (no tools registered)"

    def _build_prompt(self, question: str, history: List[Dict]) -> str:
        history_text = ""
        for h in history:
            history_text += f"\nThought: {h['thought']}\nTool: {h['tool']}\nResult: {h['result']}\n"

        return f"""{self.system_prompt}

Available tools:
{self._tool_descriptions()}

Question: {question}
{history_text}
Next action (JSON only):"""

    def _call_llm(self, prompt: str) -> Dict:
        resp = ollama.chat(
            model=self._get_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"num_predict": 400, "temperature": 0.0},
        )
        raw = resp["message"]["content"].strip()
        raw = re.sub(r"```[a-z]*\s*", "", raw).strip("`").strip()
        start = raw.find("{")
        if start == -1:
            raise ValueError(f"LLM returned non-JSON: {raw[:200]}")
        # Walk forward to find the first complete JSON object (brace-balanced)
        depth = 0
        in_str = False
        escape = False
        end = start
        for i, ch in enumerate(raw[start:], start):
            if escape:
                escape = False
                continue
            if ch == "\\" and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        return json.loads(raw[start:end])

    def _execute_tool(self, tool_name: str, args: Dict) -> Any:
        if tool_name not in self._tools:
            return f"Unknown tool: {tool_name}. Available: {list(self._tools.keys())}"
        fn = self._tools[tool_name]
        sig = inspect.signature(fn)
        accepts_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if not accepts_kwargs:
            valid = {k: v for k, v in args.items() if k in sig.parameters}
            dropped = set(args) - set(valid)
            if dropped:
                logger.debug(f"[{tool_name}] dropped unknown args: {dropped}")
            args = valid
        try:
            return fn(**args)
        except Exception as e:
            return f"Tool error: {e}"

    def run(self, question: str, doc_ids: List[str] = None) -> AgentResult:
        """
        ReAct loop: reason → act → observe → repeat until finish or max iterations.
        """
        doc_ids = doc_ids or []
        history: List[Dict] = []
        steps: List[Dict] = []
        chart_data = None

        for i in range(MAX_ITERATIONS):
            t0 = time.time()
            try:
                prompt = self._build_prompt(question, history)
                action = self._call_llm(prompt)
            except Exception as e:
                logger.warning(f"[{self.domain}] LLM parse failed iter {i}: {e}")
                break

            thought = action.get("thought", "")
            tool_name = action.get("tool", "finish")
            args = action.get("args", {})

            # Inject doc_ids into every tool call automatically
            if doc_ids and "doc_ids" not in args:
                args["doc_ids"] = doc_ids

            if tool_name == "finish":
                answer = args.get("answer", "")
                steps.append({
                    "thought": thought, "tool": "finish",
                    "observation": answer,
                    "latency_ms": int((time.time() - t0) * 1000),
                })
                return AgentResult(
                    answer=answer,
                    sources=self._collect_sources(history),
                    steps=steps,
                    chart_data=chart_data,
                    domain=self.domain,
                )

            result = self._execute_tool(tool_name, args)

            # Check if tool returned chart data alongside text
            if isinstance(result, dict) and "chart_data" in result:
                chart_data = result.pop("chart_data")
                result = result.get("text", str(result))

            history.append({
                "thought": thought,
                "tool":    tool_name,
                "result":  str(result)[:1000],  # cap to avoid prompt explosion
            })
            steps.append({
                "thought":     thought,
                "tool":        tool_name,
                "args":        args,
                "observation": str(result)[:500],
                "latency_ms":  int((time.time() - t0) * 1000),
            })

        # Fallback if max iterations hit without finish
        last_result = history[-1]["result"] if history else "No results found."
        return AgentResult(
            answer=last_result,
            sources=self._collect_sources(history),
            steps=steps,
            chart_data=chart_data,
            domain=self.domain,
        )

    def _collect_sources(self, history: List[Dict]) -> List[str]:
        sources = []
        for h in history:
            result = h.get("result", "")
            for match in re.findall(r'\b[\w\-]+\.pdf\b', result, re.IGNORECASE):
                if match not in sources:
                    sources.append(match)
        return sources

    def __del__(self):
        if getattr(self, "_own_db", False):
            try:
                self._db.close()
            except Exception:
                pass
