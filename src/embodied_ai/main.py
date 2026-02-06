"""Main entry point for Embodied AI."""

import asyncio
import os
import sys

from dotenv import load_dotenv

from .bot import Bot
from .claude_client import ClaudeClient
from .config_loader import load_config, resolve_bot_paths, resolve_system_prompt
from .mcp_client import MCPClient


async def async_main(autonomous: bool = False, config_file: str | None = None) -> None:
    """Async main function."""
    # Load environment variables
    load_dotenv()

    # Load config
    try:
        config, config_path = load_config(config_file)
        system_prompt = resolve_system_prompt(config, config_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        print("Please create a valid config.json and Claude prompt file.")
        sys.exit(1)

    # Initialize clients
    mcp_client = MCPClient()
    claude_config = config.get("claude", {})
    claude_client = ClaudeClient(
        model=claude_config.get("model", "claude-sonnet-4-20250514"),
        system_prompt=system_prompt,
    )

    # Connect to MCP servers
    print("Connecting to MCP servers...")
    await mcp_client.connect(config.get("mcpServers", {}))

    # Create bot
    bot_config = config.get("bot", {})
    memory_path, desire_path, self_path = resolve_bot_paths(config, config_path)

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
    print("Embodied AI started!")
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
                print("\n[Input Error] Character encoding issue. Please try again.\n")
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


def run_web_server(
    config_file: str | None = None,
    host: str | None = None,
    port: int | None = None,
) -> None:
    """Run FastAPI web server for the PWA frontend."""
    from .web_app import create_app

    import uvicorn

    try:
        config, config_path = load_config(config_file)
        resolve_system_prompt(config, config_path)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error: {e}")
        print("Please create a valid config.json and Claude prompt file.")
        sys.exit(1)

    web_config = config.get("web", {})
    resolved_host = host or web_config.get("host", "0.0.0.0")
    resolved_port = port if port is not None else int(web_config.get("port", 8000))

    if config_file:
        os.environ["EMBODIED_AI_CONFIG"] = config_file

    app = create_app()
    uvicorn.run(app, host=resolved_host, port=resolved_port)


def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Embodied AI")
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
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run web server with PWA frontend",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Web server host (used with --web)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Web server port (used with --web)",
    )
    args = parser.parse_args()

    if args.web:
        run_web_server(config_file=args.config, host=args.host, port=args.port)
        return

    asyncio.run(async_main(autonomous=args.autonomous, config_file=args.config))


if __name__ == "__main__":
    main()
