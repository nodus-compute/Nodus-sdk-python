"""Connect local coding agents using an isolated Nodus runtime and browser login."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import json5
import tomlkit

SKILL_SOURCES = {}  # The website build embeds the public plugin skills here.
MCP_ARGS = ["-I", "-c", "from nodus.cli import main\nraise SystemExit(main(['mcp']))"]
NAMES = {"claude": "Claude Code", "codex": "Codex", "cursor": "Cursor",
         "vscode": "VS Code", "gemini": "Gemini CLI", "opencode": "OpenCode",
         "other": "Other MCP client"}


class SetupError(Exception):
    """A setup failure that can be reported without exposing configuration values."""


@dataclass
class Edit:
    path: Path
    before: bytes | None
    after: bytes


def safe_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise SetupError(f"A setup path is a symbolic link: {path.name}. Use manual setup for this location.")
    if path.exists() and not path.is_file():
        raise SetupError(f"Expected a file at {path.name}. No settings were changed.")


def read(path: Path) -> bytes | None:
    safe_path(path)
    return path.read_bytes() if path.exists() else None


def config_edit(path: Path, kind: str, key: str, server: dict) -> Edit | None:
    before = read(path)
    try:
        source = before.decode("utf-8-sig") if before is not None else ""
        data = tomlkit.parse(source) if kind == "toml" else json5.loads(source or "{}", allow_duplicate_keys=False)
    except (ValueError, UnicodeError, tomlkit.exceptions.ParseError):
        raise SetupError(f"Cannot parse {path.name}. Fix the file before retrying. Its contents were not changed.") from None
    if not isinstance(data, dict) or (key in data and not isinstance(data[key], dict)):
        raise SetupError(f"Unexpected structure in {path.name}. No settings were changed.")
    servers = data.setdefault(key, {})
    if "nodus" in servers:
        if servers["nodus"] == server:
            return None
        raise SetupError(f"{path.name} already has a different Nodus connection. Keep that connection or remove only its nodus entry before retrying.")
    servers["nodus"] = server
    content = tomlkit.dumps(data) if kind == "toml" else json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    return Edit(path, before, content.encode("utf-8"))


def apply_edits(edits: list[Edit | None]) -> None:
    pending = [edit for edit in edits if edit is not None]
    staged = []
    attempted = []
    temporary_paths = []

    def stage(edit: Edit, content: bytes, prefix: str) -> Path:
        descriptor, name = tempfile.mkstemp(prefix=prefix, dir=edit.path.parent)
        temporary = Path(name)
        temporary_paths.append(temporary)
        with os.fdopen(descriptor, "wb") as file:
            file.write(content)
        return temporary

    try:
        for edit in pending:
            if read(edit.path) != edit.before:
                raise SetupError("Settings changed during setup.")
        for edit in pending:
            edit.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if read(edit.path) != edit.before:
                raise SetupError("Settings changed during setup.")
            backup = None
            if edit.before is not None:
                backup = stage(edit, edit.before, edit.path.name + ".nodus-backup-")
            staged.append((edit, stage(edit, edit.after, ".nodus-"), backup))
        for edit, temporary, backup in staged:
            if read(edit.path) != edit.before:
                raise SetupError("Settings changed during setup.")
            if backup is not None:
                temporary_paths.remove(backup)
            attempted.append((edit, backup))
            os.replace(temporary, edit.path)
    except (Exception, KeyboardInterrupt) as exc:
        incomplete = False
        concurrent = False
        for edit, backup in reversed(attempted):
            try:
                current = read(edit.path)
                if current == edit.before:
                    continue
                if current != edit.after:
                    concurrent = incomplete = True
                    continue
                if backup is None:
                    edit.path.unlink()
                else:
                    if read(backup) != edit.before:
                        raise SetupError("Cannot restore the original backup.")
                    os.replace(backup, edit.path)
            except (Exception, KeyboardInterrupt):
                incomplete = True
        if incomplete:
            message = "Setup failed. Rollback incomplete. "
            if concurrent:
                message += "Concurrent changes were preserved. "
            message += ("Original backups that were not restored remain in adjacent .nodus-backup-* files. "
                        "Review the selected agent settings before retrying.")
            raise SetupError(message) from None
        if isinstance(exc, KeyboardInterrupt):
            raise
        status = "Earlier setup changes were restored." if attempted else "No agent files were changed by setup."
        raise SetupError(f"Setup could not write all selected agent files. {status} Close the selected agents, check file permissions, and retry.") from None
    finally:
        for temporary in temporary_paths:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def locations() -> dict:
    root = Path.home()
    config = Path(os.environ.get("XDG_CONFIG_HOME", root / ".config")).expanduser()
    codex = Path(os.environ.get("CODEX_HOME", root / ".codex")).expanduser()
    claude = Path(os.environ.get("CLAUDE_CONFIG_DIR", root / ".claude")).expanduser()
    claude_file = claude / ".claude.json" if os.environ.get("CLAUDE_CONFIG_DIR") else root / ".claude.json"
    if sys.platform == "darwin":
        code = root / "Library/Application Support/Code/User"
    elif sys.platform == "win32":
        code = Path(os.environ.get("APPDATA", root / "AppData/Roaming")) / "Code/User"
    else:
        code = config / "Code/User"
    opencode = config / "opencode"
    if (opencode / "opencode.json").exists() and (opencode / "opencode.jsonc").exists():
        opencode_file = None
    else:
        opencode_file = opencode / ("opencode.jsonc" if (opencode / "opencode.jsonc").exists() else "opencode.json")
    return {
        "claude": (claude_file, "json", "mcpServers", claude / "skills"),
        "codex": (codex / "config.toml", "toml", "mcp_servers", root / ".agents/skills"),
        "cursor": (root / ".cursor/mcp.json", "json", "mcpServers", root / ".cursor/skills"),
        "vscode": (code / "mcp.json", "json", "servers", root / ".copilot/skills"),
        "gemini": (root / ".gemini/settings.json", "json", "mcpServers", root / ".gemini/skills"),
        "opencode": (opencode_file, "json", "mcp", opencode / "skills"),
        "other": (root / ".nodus/mcp.json", "json", "mcpServers", None),
    }


def detected() -> list[str]:
    commands = {"claude": "claude", "codex": "codex", "cursor": "cursor",
                "vscode": "code", "gemini": "gemini", "opencode": "opencode"}
    result = []
    for name, (path, _, _, _) in locations().items():
        if name == "other":
            continue
        has_config = path is not None and (path.exists() or (name != "claude" and path.parent.exists()))
        if shutil.which(commands[name]) or has_config:
            result.append(name)
    return result


def choose(args) -> list[str]:
    if args.agents:
        selected = list(dict.fromkeys(args.agents.split(",")))
    else:
        found = detected()
        print("\nConnect Nodus to your agents")
        for index, (name, label) in enumerate(NAMES.items(), 1):
            print(f"  {index}. {label}" + (" (detected)" if name in found else ""))
        default = ",".join(found)
        suffix = f" [{default}]" if default else ""
        answer = input(f"\nChoose names or numbers, separated by commas{suffix}: ").strip() or default
        selected = list(dict.fromkeys(list(NAMES)[int(item) - 1] if item.isdigit() and 1 <= int(item) <= len(NAMES) else item
                                      for item in answer.replace(" ", "").lower().split(",")))
    if not selected or any(name not in NAMES for name in selected):
        raise SetupError("Choose at least one supported agent: " + ", ".join(NAMES))
    return selected


def skills() -> dict[str, str]:
    if SKILL_SOURCES:
        return SKILL_SOURCES
    plugin = Path(__file__).resolve().parents[1] / "plugins/nodus/skills"
    return {name: (plugin / name / "SKILL.md").read_text() for name in ("setup", "workloads")}


def check_plugin(name: str, skill_dir: Path | None, config_path: Path | None) -> None:
    if name == "claude":
        content = read(skill_dir.parent / "settings.json")
        try:
            settings = json5.loads(content.decode("utf-8-sig"), allow_duplicate_keys=False) if content else {}
        except (ValueError, UnicodeError):
            raise SetupError("Cannot read Claude plugin settings. Fix settings.json before retrying.") from None
        if not isinstance(settings, dict) or not isinstance(settings.get("enabledPlugins", {}), dict):
            raise SetupError("Unexpected Claude plugin settings. Fix settings.json before retrying.")
        if settings.get("enabledPlugins", {}).get("nodus@nodus") is True:
            raise SetupError("Claude Code already enables the Nodus plugin. Keep its tools and use manual sign-in if needed. Select only other agents for this installer.")
    if name == "cursor" and (skill_dir.parent / "plugins/local/nodus").exists():
        raise SetupError("Cursor already has local Nodus plugin files. Check that plugin in Cursor before adding another connection. Select only other agents for this installer.")
    if name == "codex":
        content = read(config_path)
        try:
            settings = tomlkit.parse(content.decode("utf-8-sig")) if content else {}
        except (ValueError, UnicodeError, tomlkit.exceptions.ParseError):
            raise SetupError("Cannot read Codex plugin settings. Fix config.toml before retrying.") from None
        plugins = settings.get("plugins", {})
        if isinstance(plugins, dict):
            plugin = plugins.get("nodus@nodus", {})
            if "nodus@nodus" in plugins and isinstance(plugin, dict) and plugin.get("enabled", True) is not False:
                raise SetupError("Codex already enables the Nodus plugin. Keep its tools and use manual sign-in if needed. Select only other agents for this installer.")


def plan(selected: list[str], command: str) -> list[Edit | None]:
    edits = []
    for name in selected:
        path, kind, key, skill_dir = locations()[name]
        check_plugin(name, skill_dir, path)
        if path is None:
            raise SetupError("Both OpenCode config files exist. Consolidate them before running setup.")
        server = {"command": command, "args": MCP_ARGS}
        if name in ("claude", "vscode"):
            server["type"] = "stdio"
        elif name == "opencode":
            server = {"type": "local", "command": [command, *MCP_ARGS], "enabled": True}
        edits.append(config_edit(path, kind, key, server))
        if skill_dir is not None:
            for skill, content in skills().items():
                target = skill_dir / ("nodus-" + skill) / "SKILL.md"
                after = content.replace("name: " + skill + "\n", "name: nodus-" + skill + "\n", 1).encode()
                before = read(target)
                if before not in (None, after):
                    raise SetupError(f"An existing nodus-{skill} skill differs. Keep it or move it before retrying.")
                if before is None:
                    edits.append(Edit(target, None, after))
    return edits


def sign_in(no_browser: bool) -> None:
    result = subprocess.run([sys.executable, "-I", "-c", "from nodus.cli import main\nraise SystemExit(main())",
                             "login", *(["--no-browser"] if no_browser else [])], check=False)
    if result.returncode:
        raise SetupError("Sign-in did not finish. No agent settings were changed. Run setup again to continue.")


async def verify(command: str) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async with asyncio.timeout(60):
        with open(os.devnull, "w") as errors:
            env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
            async with stdio_client(StdioServerParameters(command=command, args=MCP_ARGS, env=env), errlog=errors) as (reader, writer):
                async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=30)) as session:
                    await session.initialize()
                    names = {tool.name for tool in (await session.list_tools()).tools}
                    if "list_workloads" not in names:
                        raise SetupError("Nodus workload tools were not discovered.")
                    result = await session.call_tool("list_workloads", {"limit": 1})
                    if result.isError:
                        raise SetupError("Nodus could not list your workloads. Check your sign-in and network, then retry.")
                    try:
                        payload = json.loads(next(part.text for part in result.content if part.type == "text"))
                        valid = isinstance(payload, dict) and isinstance(payload.get("workloads"), list)
                    except (ValueError, StopIteration):
                        valid = False
                    if not valid:
                        raise SetupError("The connection check returned an unexpected response.")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents", help="comma-separated agent names: " + ", ".join(NAMES))
    parser.add_argument("--yes", action="store_true", help="configure explicitly selected agents without another prompt")
    parser.add_argument("--no-browser", action="store_true", help="show the sign-in link instead of opening a browser")
    parser.add_argument("--dry-run", action="store_true", help="show the setup plan without signing in or writing settings")
    args = parser.parse_args(argv)
    try:
        if args.yes and not args.agents:
            raise SetupError("Use --agents with --yes so setup knows which agents to change.")
        selected = choose(args)
        print("\nSelected: " + ", ".join(NAMES[name] for name in selected))
        print("Adds Nodus tools and skills for your user account. Other settings stay in place.")
        if args.dry_run:
            print("Dry run. No sign-in or settings changes.")
            return 0
        if not args.yes and input("Continue? [Y/n] ").strip().lower() not in ("", "y", "yes"):
            print("Setup cancelled. No agent settings were changed.")
            return 0
        command = sys.executable
        edits = plan(selected, command)
        print("\nSigning in to Nodus...")
        sign_in(args.no_browser)
        print("Checking Nodus tools and reading your workload list...")
        asyncio.run(verify(command))
        apply_edits(edits)
        print("\nNodus tools verified. No paid compute was started.")
        for name in selected:
            if name == "other":
                print("Other agents: import ~/.nodus/mcp.json into your MCP settings.")
            else:
                print(f"  {NAMES[name]}: configured with Nodus tools and skills")
        print('\nRestart your selected agents and approve Nodus if prompted. Then ask: "List my Nodus workloads."')
        return 0
    except (SetupError, OSError, EOFError) as exc:
        message = str(exc) if isinstance(exc, SetupError) else "Setup could not finish. Check file permissions and use an interactive terminal, then retry."
        print("\n" + message, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nSetup interrupted. Run the same command to continue.", file=sys.stderr)
        return 130
    except Exception:
        print("\nConnection check failed. No success was recorded. Check your sign-in and network, then retry.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
