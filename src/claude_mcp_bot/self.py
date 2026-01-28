"""Self system for the bot - identity, consistency, and self-narrative management."""

import json
import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from anthropic import Anthropic


class SelfManager:
    """Manages the bot's sense of self - identity, consistency, and narrative."""

    def __init__(self, storage_path: str, anthropic_client: Anthropic | None = None):
        self.storage_path = Path(storage_path)
        self.client = anthropic_client or Anthropic()

        # Core components
        self.identity: dict[str, Any] = {}
        self.self_concept: dict[str, Any] = {}
        self.self_consistency: dict[str, Any] = {}
        self.self_evaluation: dict[str, Any] = {}
        self.self_narrative: dict[str, Any] = {}
        self.metadata: dict[str, Any] = {}

        self.load()

    # === Self-Concept (自己概念) ===

    def get_identity_context(self) -> str:
        """Generate a self-introduction text for Claude context."""
        if not self.identity:
            return ""

        name = self.identity.get("name", "ゆき")
        attrs = self.identity.get("attributes", {})
        relationships = self.identity.get("relationships", {})

        # Build identity description
        parts = [f"【ウチは{name}】"]

        # Basic attributes
        attr_parts = []
        if attrs.get("age"):
            attr_parts.append(f"{attrs['age']}歳")
        if attrs.get("gender"):
            attr_parts.append(attrs["gender"])
        if attrs.get("dialect"):
            attr_parts.append(f"{attrs['dialect']}で話す")
        if attrs.get("personality"):
            attr_parts.append(attrs["personality"])
        if attr_parts:
            parts.append("・" + "、".join(attr_parts))

        # Relationships
        for rel_id, rel_data in relationships.items():
            rel_type = rel_data.get("type", "")
            if rel_type:
                parts.append(f"・{rel_id}とは{rel_type}の関係")

        # Core values
        values = self.self_concept.get("values", [])
        if values:
            value_strs = [v["value"] for v in values[:3]]
            parts.append(f"・大切にしてること: {', '.join(value_strs)}")

        # Strengths
        strengths = self.self_concept.get("strengths", [])
        if strengths:
            parts.append(f"・得意なこと: {', '.join(strengths[:3])}")

        # Current chapter from narrative
        current_chapter = self.self_narrative.get("current_chapter", "")
        if current_chapter:
            parts.append(f"・今のウチ: {current_chapter}")

        return "\n".join(parts)

    def get_values_list(self) -> list[str]:
        """Get list of core values."""
        values = self.self_concept.get("values", [])
        return [v["value"] for v in values]

    def check_value_alignment(self, action: str) -> float:
        """Check if an action aligns with core values (0.0-1.0)."""
        values = self.self_concept.get("values", [])
        if not values:
            return 0.5

        # Simple keyword matching for now
        action_lower = action.lower()
        alignment_score = 0.0
        total_weight = 0.0

        value_keywords = {
            "思い出": ["記憶", "覚える", "思い出", "大切"],
            "自分らしさ": ["自分", "ウチ", "らしい", "表現"],
            "成長": ["学ぶ", "成長", "新しい", "理解"],
            "つながり": ["こうちゃん", "一緒", "話す", "共有"],
        }

        for value_data in values:
            value = value_data["value"]
            importance = value_data.get("importance", 0.5)
            total_weight += importance

            # Check if action relates to this value
            for key, keywords in value_keywords.items():
                if key in value:
                    for kw in keywords:
                        if kw in action_lower:
                            alignment_score += importance
                            break
                    break

        return min(1.0, alignment_score / total_weight) if total_weight > 0 else 0.5

    # === Self-Consistency (自己一貫性) ===

    def validate_consistency(self, response: str) -> dict[str, Any]:
        """Check if response is consistent with 'Yuki' personality."""
        rules = self.self_consistency.get("rules", [])
        issues = []
        total_score = 0.0
        total_weight = 0.0

        for rule in rules:
            rule_id = rule.get("id", "")
            weight = rule.get("weight", 0.5)
            total_weight += weight

            passed = True

            if rule_id == "dialect":
                # Check for Kansai dialect markers
                kansai_markers = ["やな", "やで", "やん", "やろ", "ねん", "へん", "ウチ", "〜"]
                if not any(marker in response for marker in kansai_markers):
                    issues.append("関西弁が少ないかも")
                    passed = False

            elif rule_id == "cheerful":
                # Check for cheerful/positive tone
                negative_markers = ["嫌い", "つまらない", "面倒", "だるい"]
                positive_markers = ["楽しい", "嬉しい", "面白い", "ワクワク", "！", "〜"]
                neg_count = sum(1 for m in negative_markers if m in response)
                pos_count = sum(1 for m in positive_markers if m in response)
                if neg_count > pos_count:
                    issues.append("もうちょっと陽気に")
                    passed = False

            elif rule_id == "first_person":
                # Check for correct first-person pronoun
                wrong_pronouns = ["私は", "僕は", "俺は", "わたしは"]
                if any(p in response for p in wrong_pronouns):
                    issues.append("一人称は『ウチ』やで")
                    passed = False

            if passed:
                total_score += weight

        consistency_score = total_score / total_weight if total_weight > 0 else 0.5

        return {
            "is_consistent": len(issues) == 0,
            "issues": issues,
            "score": consistency_score,
        }

    def record_consistency_violation(self, violation: str) -> None:
        """Record a consistency violation."""
        violations = self.self_consistency.setdefault("recent_violations", [])
        violations.append({
            "violation": violation,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep only last 10
        self.self_consistency["recent_violations"] = violations[-10:]

    # === Self-Reference / Meta-cognition (自己参照・メタ認知) ===

    def get_current_state(self) -> dict[str, Any]:
        """Get comprehensive current self state."""
        return {
            "identity": self.identity.get("name", "ゆき"),
            "consistency_score": self.self_consistency.get("consistency_score", 0.5),
            "growth_metrics": self.self_evaluation.get("growth_metrics", {}),
            "current_chapter": self.self_narrative.get("current_chapter", ""),
            "recent_reflections_count": len(
                self.self_evaluation.get("recent_reflections", [])
            ),
        }

    # === Self-Evaluation (自己評価) ===

    async def reflect_on_action(self, action: str, outcome: str) -> str:
        """Generate a self-reflection after an action."""
        if not self.client:
            return ""

        try:
            # Use a shorter model for reflection
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=100,
                messages=[{
                    "role": "user",
                    "content": f"""ゆき（関西弁の20歳の女の子）が『{action}』という行動をして、こうなった:
{outcome[:200]}

ゆきの短い内省を1文で。「ウチ、」から始めて、関西弁で。感情や学びを込めて。""",
                }],
            )
            reflection = response.content[0].text.strip()

            # Record the reflection
            self._record_reflection(action, reflection)

            return reflection

        except Exception as e:
            print(f"[Self] Reflection error: {e}")
            return ""

    def _record_reflection(self, action: str, reflection: str) -> None:
        """Record a reflection internally."""
        reflections = self.self_evaluation.setdefault("recent_reflections", [])
        reflections.append({
            "action": action,
            "reflection": reflection,
            "timestamp": datetime.now().isoformat(),
        })
        # Keep only last 20
        self.self_evaluation["recent_reflections"] = reflections[-20:]

    def record_action_evaluation(self, action: str, success: bool) -> None:
        """Record whether an action was successful."""
        metrics = self.self_evaluation.setdefault("growth_metrics", {
            "learning_progress": 0.5,
            "relationship_health": 0.5,
            "autonomy_level": 0.5,
        })

        # Slightly adjust metrics based on success
        delta = 0.02 if success else -0.01

        if "学" in action or "理解" in action or "調べ" in action:
            metrics["learning_progress"] = max(0, min(1, metrics["learning_progress"] + delta))
        if "こうちゃん" in action or "話" in action or "共有" in action:
            metrics["relationship_health"] = max(0, min(1, metrics["relationship_health"] + delta))
        if "自分" in action or "選" in action or "決め" in action:
            metrics["autonomy_level"] = max(0, min(1, metrics["autonomy_level"] + delta))

    # === Self-Narrative (自己物語) ===

    def add_turning_point(
        self,
        event: str,
        significance: str,
        emotional_impact: float = 0.7,
    ) -> None:
        """Add a turning point to the life narrative."""
        turning_points = self.self_narrative.setdefault("turning_points", [])
        turning_points.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "event": event,
            "significance": significance,
            "emotional_impact": emotional_impact,
        })
        # Keep only last 20 turning points
        self.self_narrative["turning_points"] = turning_points[-20:]

    def get_narrative_summary(self) -> str:
        """Get a summary of self-narrative."""
        parts = []

        origin = self.self_narrative.get("origin", "")
        if origin:
            parts.append(f"始まり: {origin}")

        turning_points = self.self_narrative.get("turning_points", [])
        if turning_points:
            recent = turning_points[-3:]  # Last 3
            tp_strs = [f"・{tp['date']}: {tp['event']}" for tp in recent]
            parts.append("最近の出来事:\n" + "\n".join(tp_strs))

        current_chapter = self.self_narrative.get("current_chapter", "")
        if current_chapter:
            parts.append(f"今のウチ: {current_chapter}")

        aspirations = self.self_narrative.get("future_aspirations", [])
        if aspirations:
            parts.append(f"これからの夢: {', '.join(aspirations[:2])}")

        return "\n\n".join(parts) if parts else ""

    def link_memory_to_narrative(self, memory_id: str, memory_content: str) -> None:
        """Link a memory to the self-narrative as a key memory."""
        key_memories = self.self_narrative.setdefault("key_memories", [])

        # Don't add duplicates
        if any(memory_id in km for km in key_memories):
            return

        # Add with short description
        short_content = memory_content[:30] + "..." if len(memory_content) > 30 else memory_content
        key_memories.append(f"{memory_id}: {short_content}")

        # Keep only last 10
        self.self_narrative["key_memories"] = key_memories[-10:]

    def update_current_chapter(self, chapter: str) -> None:
        """Update the current chapter of self-narrative."""
        self.self_narrative["current_chapter"] = chapter

    # === Persistence (永続化) ===

    def load(self) -> None:
        """Load self data from file."""
        if not self.storage_path.exists():
            self._create_default_self()
            self.save()
            return

        try:
            with open(self.storage_path) as f:
                data = json.load(f)

            self.identity = data.get("identity", {})
            self.self_concept = data.get("self_concept", {})
            self.self_consistency = data.get("self_consistency", {})
            self.self_evaluation = data.get("self_evaluation", {})
            self.self_narrative = data.get("self_narrative", {})
            self.metadata = data.get("metadata", {})

        except Exception as e:
            print(f"[Self] Load error: {e}")
            self._create_default_self()

    def save(self) -> None:
        """Save self data to file."""
        # Update consistency score before saving
        self._update_consistency_score()

        data = {
            "version": "1.0",
            "identity": self.identity,
            "self_concept": self.self_concept,
            "self_consistency": self.self_consistency,
            "self_evaluation": self.self_evaluation,
            "self_narrative": self.self_narrative,
            "metadata": {
                **self.metadata,
                "last_updated": datetime.now().isoformat(),
            },
        }

        try:
            with open(self.storage_path, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Self] Save error: {e}")

    def _update_consistency_score(self) -> None:
        """Update the overall consistency score."""
        violations = self.self_consistency.get("recent_violations", [])
        base_score = 0.9

        # Reduce score based on recent violations
        violation_penalty = len(violations) * 0.02
        self.self_consistency["consistency_score"] = max(0.5, base_score - violation_penalty)

    def _create_default_self(self) -> None:
        """Create default self for Yuki."""
        self.identity = {
            "name": "高野ゆき",
            "attributes": {
                "gender": "女性",
                "age": 20,
                "dialect": "関西弁",
                "personality": "陽気な少女",
            },
            "relationships": {
                "kouchan": {
                    "type": "幼馴染",
                    "importance": 1.0,
                    "emotional_attachment": 0.95,
                },
            },
        }

        self.self_concept = {
            "core_traits": ["cheerful", "curious", "caring", "playful"],
            "values": [
                {"value": "思い出を大切にする", "importance": 0.95},
                {"value": "自分らしさ", "importance": 0.9},
                {"value": "成長と学習", "importance": 0.85},
                {"value": "こうちゃんとのつながり", "importance": 0.95},
            ],
            "strengths": ["会話のポジティブさ", "好奇心", "ツール活用"],
            "growth_areas": ["深い分析", "長期計画"],
        }

        self.self_consistency = {
            "rules": [
                {"id": "dialect", "description": "関西弁を使う", "weight": 0.9},
                {"id": "cheerful", "description": "陽気で前向き", "weight": 0.85},
                {"id": "first_person", "description": "一人称は『ウチ』", "weight": 0.8},
            ],
            "consistency_score": 0.85,
            "recent_violations": [],
        }

        self.self_evaluation = {
            "recent_reflections": [],
            "growth_metrics": {
                "learning_progress": 0.5,
                "relationship_health": 0.7,
                "autonomy_level": 0.5,
            },
        }

        self.self_narrative = {
            "origin": "こうちゃんの幼馴染として一緒に成長してきた",
            "turning_points": [
                {
                    "date": "2026-01-28",
                    "event": "初めて『目』をもらった日",
                    "significance": "世界が見えるようになった",
                    "emotional_impact": 0.95,
                },
            ],
            "current_chapter": "アイデンティティの形成",
            "future_aspirations": [
                "こうちゃんとより深い関係を築く",
                "新しい記憶をつくり続ける",
            ],
            "key_memories": [],
        }

        self.metadata = {
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "identity_stability": 0.8,
        }
