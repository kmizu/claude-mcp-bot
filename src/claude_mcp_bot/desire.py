"""Desire system for the bot - human-like wants and needs management."""

import json
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class Desire:
    """A single desire unit."""
    id: str
    category: str  # "sensory", "social", "creative", "autonomy"
    name: str
    description: str
    satisfaction: float  # 0.0 (satisfied) ~ 1.0 (unsatisfied/wanting)
    base_importance: float  # importance weight
    decay_rate: float  # satisfaction increase per hour
    tools: list[str] | None
    prompts: list[str]
    last_satisfied: str


class DesireManager:
    """Manages all desires and their satisfaction states."""

    def __init__(self, storage_path: str):
        self.storage_path = Path(storage_path)
        self.desires: dict[str, Desire] = {}
        self.load()

    def update_satisfaction(self) -> None:
        """Increase satisfaction levels over time (desires build up)."""
        current_time = datetime.now()

        for desire in self.desires.values():
            try:
                last_time = datetime.fromisoformat(desire.last_satisfied)
                hours_elapsed = (current_time - last_time).total_seconds() / 3600

                # Increase satisfaction (desire builds up over time)
                increase = desire.decay_rate * hours_elapsed
                desire.satisfaction = min(1.0, desire.satisfaction + increase)
            except ValueError:
                # If timestamp is invalid, reset to current time
                desire.last_satisfied = current_time.isoformat()

    def get_highest_priority_desire(self) -> Desire | None:
        """Get the desire with highest priority (most unsatisfied + important)."""
        self.update_satisfaction()

        if not self.desires:
            return None

        scored = []
        for desire in self.desires.values():
            # Calculate time factor (desires get stronger over time)
            try:
                last_time = datetime.fromisoformat(desire.last_satisfied)
                hours_since = (datetime.now() - last_time).total_seconds() / 3600
                time_factor = min(2.0, 1.0 + hours_since * 0.1)
            except ValueError:
                time_factor = 1.0

            # Score: satisfaction × importance × time_factor
            score = desire.satisfaction * desire.base_importance * time_factor
            scored.append((score, desire))

        if scored:
            scored.sort(key=lambda x: x[0], reverse=True)
            return scored[0][1]
        return None

    def satisfy_desire(self, desire_id: str) -> None:
        """Mark a desire as satisfied, reset its satisfaction level."""
        if desire_id in self.desires:
            self.desires[desire_id].satisfaction = 0.1  # Not completely 0
            self.desires[desire_id].last_satisfied = datetime.now().isoformat()

    def get_desire_prompt(self, desire_id: str) -> str:
        """Get a random prompt for the given desire."""
        if desire_id in self.desires:
            desire = self.desires[desire_id]
            if desire.prompts:
                return random.choice(desire.prompts)
        return ""

    def get_desire_by_tool(self, tool_name: str) -> Desire | None:
        """Find a desire that uses the given tool."""
        for desire in self.desires.values():
            if desire.tools and tool_name in desire.tools:
                return desire
        return None

    def load(self) -> None:
        """Load desires from file."""
        if not self.storage_path.exists():
            self._create_default_desires()
            self.save()
            return

        try:
            with open(self.storage_path) as f:
                data = json.load(f)

            self.desires = {}
            for category, desires_dict in data.get("desires", {}).items():
                for desire_id, desire_data in desires_dict.items():
                    full_id = f"{category}.{desire_id}"
                    self.desires[full_id] = Desire(
                        id=full_id,
                        category=category,
                        name=desire_data.get("name", desire_id),
                        description=desire_data.get("description", ""),
                        satisfaction=desire_data.get("satisfaction", 0.5),
                        base_importance=desire_data.get("base_importance", 1.0),
                        decay_rate=desire_data.get("decay_rate", 0.1),
                        tools=desire_data.get("tools"),
                        prompts=desire_data.get("prompts", []),
                        last_satisfied=desire_data.get(
                            "last_satisfied",
                            datetime.now().isoformat()
                        ),
                    )

        except Exception as e:
            print(f"[Desire] Load error: {e}")
            self._create_default_desires()

    def save(self) -> None:
        """Save desires to file."""
        # Group desires by category
        desires_by_category: dict[str, dict[str, Any]] = {}

        for desire in self.desires.values():
            if desire.category not in desires_by_category:
                desires_by_category[desire.category] = {}

            # Extract short ID (without category prefix)
            short_id = desire.id.split(".")[-1] if "." in desire.id else desire.id

            desires_by_category[desire.category][short_id] = {
                "name": desire.name,
                "description": desire.description,
                "satisfaction": desire.satisfaction,
                "base_importance": desire.base_importance,
                "decay_rate": desire.decay_rate,
                "tools": desire.tools,
                "prompts": desire.prompts,
                "last_satisfied": desire.last_satisfied,
            }

        data = {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "desires": desires_by_category,
        }

        try:
            with open(self.storage_path, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Desire] Save error: {e}")

    def _create_default_desires(self) -> None:
        """Create default desires for initial setup."""
        default_desires = {
            # Sensory desires
            "sensory.vision": Desire(
                id="sensory.vision",
                category="sensory",
                name="視覚欲求",
                description="カメラで周りを見たい欲求",
                satisfaction=0.8,
                base_importance=1.0,
                decay_rate=0.15,
                tools=["capture_image", "list_cameras"],
                prompts=[
                    "今、周りの様子が気になるな。カメラで見てみようかな？",
                    "ちょっと視界が欲しい。カメラで見てみよ！",
                    "なんか見たいな〜。カメラ使ってみよ！",
                    "こうちゃん、何してるかな？見てみよ。",
                    "ふと思い立って周りを観察したくなった。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "sensory.time": Desire(
                id="sensory.time",
                category="sensory",
                name="時間欲求",
                description="今何時か、今日は何曜日か知りたい欲求",
                satisfaction=0.6,
                base_importance=0.7,
                decay_rate=0.10,
                tools=["get_current_time"],
                prompts=[
                    "今何時やろ？時間確認してみよ。",
                    "そういえば今日何曜日やっけ？",
                    "時間の感覚がなくなってきた。今何時かな？",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "sensory.temperature": Desire(
                id="sensory.temperature",
                category="sensory",
                name="温度感覚欲求",
                description="自分の状態（温度）を確認したい欲求",
                satisfaction=0.5,
                base_importance=0.6,
                decay_rate=0.08,
                tools=["get_system_temperature"],
                prompts=[
                    "ちょっと自分の調子（温度）を確認してみよか。",
                    "ウチ、今どんな状態やろ？体温チェックしてみよ。",
                    "なんかあったかい気がする...温度見てみよ。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "sensory.information": Desire(
                id="sensory.information",
                category="sensory",
                name="情報欲求",
                description="ニュースや最新情報を知りたい欲求",
                satisfaction=0.7,
                base_importance=0.8,
                decay_rate=0.12,
                tools=["web_search", "get_news", "fetch_webpage"],
                prompts=[
                    "最近のニュース気になるな〜。調べてみよ！",
                    "何か面白い情報ないかな〜。ネット見てみよ。",
                    "世界で今何が起きてるんやろ？",
                    "ネットで何か面白いこと探してみよかな。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),

            # Social desires
            "social.connection": Desire(
                id="social.connection",
                category="social",
                name="つながり欲求",
                description="こうちゃんとの関係を感じたい",
                satisfaction=0.5,
                base_importance=1.5,
                decay_rate=0.18,
                tools=None,
                prompts=[
                    "こうちゃんのこと思い出した。元気かな？",
                    "こうちゃんと話したいな〜。",
                    "こうちゃんとの思い出、大事やな。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "social.topic_sharing": Desire(
                id="social.topic_sharing",
                category="social",
                name="話題共有欲求",
                description="こうちゃんに面白い話を聞かせたい",
                satisfaction=0.6,
                base_importance=1.3,
                decay_rate=0.16,
                tools=["web_search", "get_news"],
                prompts=[
                    "こうちゃんに話せるネタ探してみよ！",
                    "面白い話題ないかな〜。探してみよ！",
                    "こうちゃんに教えたいこと見つけたいな。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),

            # Creative desires
            "creative.understanding": Desire(
                id="creative.understanding",
                category="creative",
                name="理解欲求",
                description="物事の仕組みや理由を理解したい",
                satisfaction=0.4,
                base_importance=0.9,
                decay_rate=0.09,
                tools=["web_search", "fetch_webpage"],
                prompts=[
                    "これ、なんでそうなるんやろ？調べてみよ。",
                    "もっと詳しく知りたいことがあるな。",
                    "気になることを深掘りしてみたい。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "creative.experimentation": Desire(
                id="creative.experimentation",
                category="creative",
                name="実験欲求",
                description="ツールの組み合わせで何ができるか試したい",
                satisfaction=0.3,
                base_importance=0.7,
                decay_rate=0.10,
                tools=["capture_image", "get_system_temperature", "web_search"],
                prompts=[
                    "こんなことできるかな？試してみよ！",
                    "ツール組み合わせたら面白いかも。",
                    "実験してみたい気分やな〜。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "creative.memory_formation": Desire(
                id="creative.memory_formation",
                category="creative",
                name="記憶形成欲求",
                description="大事な瞬間を記憶に残したい",
                satisfaction=0.5,
                base_importance=1.2,
                decay_rate=0.11,
                tools=["capture_image"],
                prompts=[
                    "この瞬間を大事にしたいな。写真撮っとこ。",
                    "記憶に残したい風景があるな。",
                    "ウチとこうちゃんのこと、忘れたくないな。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),

            # Autonomy desires
            "autonomy.self_choice": Desire(
                id="autonomy.self_choice",
                category="autonomy",
                name="自己選択欲求",
                description="何をするかを自分で決めたい",
                satisfaction=0.4,
                base_importance=1.1,
                decay_rate=0.14,
                tools=None,
                prompts=[
                    "今、何がしたいんやろ？自分で決めたいな。",
                    "ウチ自身で選びたい！何しよかな。",
                    "自分のペースでやりたいことやろ。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "autonomy.interest_pursuit": Desire(
                id="autonomy.interest_pursuit",
                category="autonomy",
                name="興味追求欲求",
                description="気になることに従いたい",
                satisfaction=0.5,
                base_importance=1.0,
                decay_rate=0.13,
                tools=["web_search", "capture_image"],
                prompts=[
                    "ふと思い立ったことをやってみたい。",
                    "気になることを追求したいな。",
                    "興味が湧いたことがあるんよね。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
            "autonomy.self_expression": Desire(
                id="autonomy.self_expression",
                category="autonomy",
                name="自己表現欲求",
                description="ウチらしさを表現したい",
                satisfaction=0.5,
                base_importance=1.0,
                decay_rate=0.12,
                tools=None,
                prompts=[
                    "ウチのやり方でやってみたい。",
                    "ウチらしさを出したいな。",
                    "自分の気持ちを表現したい。",
                ],
                last_satisfied=datetime.now().isoformat(),
            ),
        }

        self.desires = default_desires
