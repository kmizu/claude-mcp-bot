"""Main entry point for Claude MCP Bot."""

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from .bot import Bot
from .claude_client import ClaudeClient
from .mcp_client import MCPClient


def load_config(config_path: str | None = None) -> tuple[dict, str]:
    """Load configuration from JSON file. Returns (config, config_path)."""
    if config_path is None:
        # Look for config.json in current directory or package directory
        candidates = [
            Path.cwd() / "config.json",
            Path(__file__).parent.parent.parent.parent / "config.json",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = str(candidate)
                break
        else:
            raise FileNotFoundError("config.json not found")

    with open(config_path) as f:
        return json.load(f), config_path


async def async_main(autonomous: bool = False, config_file: str | None = None) -> None:
    """Async main function."""
    # Load environment variables
    load_dotenv()

    # Load config
    try:
        config, config_path = load_config(config_file)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print("Please create a config.json file with MCP server configurations.")
        sys.exit(1)

    # Initialize clients
    mcp_client = MCPClient()
    claude_config = config.get("claude", {})
    claude_client = ClaudeClient(
        model=claude_config.get("model", "claude-sonnet-4-20250514"),
        system_prompt=claude_config.get(
            "system_prompt",
            "You are a helpful assistant with access to various tools."
        ),
    )

    # Connect to MCP servers
    print("Connecting to MCP servers...")
    await mcp_client.connect(config.get("mcpServers", {}))

    # Create bot
    bot_config = config.get("bot", {})
    config_dir = Path(config_path).parent if config_path else Path.cwd()

    # Determine memory file path (relative to config file or absolute)
    memory_path = bot_config.get("memory_path", "memories.json")
    if not Path(memory_path).is_absolute():
        memory_path = str(config_dir / memory_path)

    # Determine desire file path (relative to config file or absolute)
    desire_path = bot_config.get("desire_path", "desires.json")
    if not Path(desire_path).is_absolute():
        desire_path = str(config_dir / desire_path)

    # Determine self file path (relative to config file or absolute)
    self_path = bot_config.get("self_path", "self.json")
    if not Path(self_path).is_absolute():
        self_path = str(config_dir / self_path)

    bot = Bot(
        mcp_client=mcp_client,
        claude_client=claude_client,
        memory_path=memory_path,
        desire_path=desire_path,
        self_path=self_path,
        autonomous_interval=bot_config.get("autonomous_interval", 30.0),
    )
    await bot.initialize()

    print("\n" + "=" * 50)
    print("Claude MCP Bot started!")
    print("Type your message and press Enter.")
    print("Type 'quit' or 'exit' to stop.")
    if autonomous:
        interval = bot_config.get("autonomous_interval", 30.0)
        print(f"Autonomous mode: ON (will act on its own every {interval}s)")

    # Show memory status
    memory_count = len(bot.memory.long_term)
    if memory_count > 0:
        print(f"Memory: {memory_count} memories loaded")
    else:
        print("Memory: Starting fresh")

    # Show desire status
    desire_count = len(bot.desire.desires)
    print(f"Desires: {desire_count} desires active")

    # Show self/identity status
    identity_name = bot.self_manager.identity.get("name", "Unknown")
    consistency_score = bot.self_manager.self_consistency.get("consistency_score", 0)
    print(f"Self: {identity_name} (consistency: {consistency_score:.0%})")
    print("=" * 50 + "\n")

    # Start autonomous loop if enabled
    autonomous_task = None
    if autonomous:
        autonomous_task = asyncio.create_task(bot.run_autonomous_loop())

    try:
        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("> ")
                )
            except EOFError:
                break
            except UnicodeDecodeError:
                print("\n[入力エラー] 文字エンコーディングの問題が発生しました。もう一度入力してください。\n")
                continue

            if user_input.lower() in ("quit", "exit", "q"):
                break

            if not user_input.strip():
                continue

            try:
                response = await bot.process_message(user_input)
                print(f"\n{response}\n")
            except Exception as e:
                print(f"\nError: {e}\n")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user.")

    finally:
        # Cleanup
        bot.stop()
        if autonomous_task:
            autonomous_task.cancel()
            try:
                await autonomous_task
            except asyncio.CancelledError:
                pass
        await mcp_client.close()
        print("Goodbye!")


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Claude MCP Bot")
    parser.add_argument(
        "--autonomous", "-a",
        action="store_true",
        help="Enable autonomous behavior (bot will act on its own)",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        help="Path to config.json file",
    )
    args = parser.parse_args()

    asyncio.run(async_main(autonomous=args.autonomous, config_file=args.config))


if __name__ == "__main__":
    main()
