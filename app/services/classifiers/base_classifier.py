# app/services/classifiers/base_classifier.py
from typing import Dict, Any, List
import ollama


class BaseClassifier:
    """Base class for all classifiers with deterministic behavior."""

    def __init__(self, model: str = "llama3.2"):
        self.model = model
        self.llm_options = {
            "temperature": 0,  # CRITICAL: No randomness
            "seed": 42,  # Consistent seed
            "top_p": 1.0,  # No nucleus sampling
            "top_k": 1  # Always most likely token
        }

    def _llm_call(self, prompt: str) -> str:
        """Deterministic LLM call."""
        response = ollama.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options=self.llm_options
        )
        return response['message']['content']

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords deterministically (sorted)."""
        if not text:
            return []

        # Simple keyword extraction (customize as needed)
        words = text.lower().split()
        # Always return sorted to ensure determinism
        return sorted(set(words))

    def _score_keywords(self, keywords: List[str], target_keywords: Dict[str, float]) -> float:
        """Score keywords deterministically."""
        score = 0.0

        # Sort both to ensure deterministic iteration
        sorted_keywords = sorted(keywords)
        sorted_targets = sorted(target_keywords.items())

        for keyword in sorted_keywords:
            for target, weight in sorted_targets:
                if target in keyword or keyword in target:
                    score += weight

        return min(score, 1.0)  # Cap at 1.0


# Update your classifiers to inherit from BaseClassifier
class SpreadsheetClassifier(BaseClassifier):
    def classify(self, sheet_data: Dict) -> Dict[str, Any]:
        # Your classification logic here
        # Use self._llm_call() for LLM calls
        # Use self._score_keywords() for keyword matching
        pass


class DocumentClassifier(BaseClassifier):
    def classify(self, parsed: Dict) -> Dict[str, Any]:
        # Your classification logic here
        pass


class ImageClassifier(BaseClassifier):
    def classify(self, parsed: Dict) -> Dict[str, Any]:
        # Your classification logic here
        pass


class EmailClassifier(BaseClassifier):
    def classify(self, parsed: Dict) -> Dict[str, Any]:
        # Your classification logic here
        pass