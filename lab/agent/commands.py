"""Safe execution of privileged Linux commands.

Every privileged action in the agent goes through :func:`run`. It takes an
argument list, never a command string, and the executable is resolved once at
start-up from a fixed allow-list of names. There is no shell anywhere in this
module, so there is no interpolation for a crafted identifier to escape into.

Commands are logged in a structured form with their exit status and a summary
of their output, which is what makes a failed policy transaction debuggable
after the fact.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from .safety import ValidationError, validate_namespace

logger = logging.getLogger("qosense.lab.commands")

# The only executables this agent may run. Resolved once, at start-up.
ALLOWED_EXECUTABLES = ("ip", "tc", "sysctl", "ping", "iperf3", "modprobe")

_resolved: dict[str, str] = {}

# Commands that take longer than this are killed rather than left running.
DEFAULT_TIMEOUT_SECONDS = 20.0


class CommandError(RuntimeError):
    """A privileged command failed."""

    def __init__(self, result: CommandResult) -> None:
        super().__init__(
            f"{' '.join(result.argv)} exited {result.returncode}: {result.stderr.strip()[:300]}"
        )
        self.result = result


@dataclass
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def structured(self) -> dict[str, object]:
        """A log-safe representation. No secrets pass through this module."""
        return {
            "argv": self.argv,
            "returncode": self.returncode,
            "duration_ms": round(self.duration_ms, 3),
            "stdout": self.stdout.strip()[:2000],
            "stderr": self.stderr.strip()[:2000],
        }


@dataclass
class CommandLog:
    """A bounded record of what the agent has run, for the audit trail."""

    limit: int = 400
    entries: list[dict[str, object]] = field(default_factory=list)

    def add(self, result: CommandResult) -> None:
        self.entries.append(result.structured())
        if len(self.entries) > self.limit:
            del self.entries[: -self.limit]

    def recent(self, count: int = 50) -> list[dict[str, object]]:
        return self.entries[-count:]


COMMAND_LOG = CommandLog()


def resolve_executables() -> dict[str, str]:
    """Resolve the allowed executables to absolute paths, once.

    Resolving at start-up means a later change to ``PATH`` cannot redirect a
    privileged command to a different binary.
    """
    if _resolved:
        return _resolved
    missing: list[str] = []
    for name in ALLOWED_EXECUTABLES:
        path = shutil.which(name)
        if path is None:
            missing.append(name)
        else:
            _resolved[name] = path
    if missing:
        logger.warning("Executables not found on this host: %s", ", ".join(missing))
    return _resolved


def executable(name: str) -> str:
    if name not in ALLOWED_EXECUTABLES:
        raise ValidationError(f"Executable '{name}' is not permitted.")
    resolved = resolve_executables().get(name)
    if resolved is None:
        raise ValidationError(
            f"Executable '{name}' is not installed. Install it on the laboratory host."
        )
    return resolved


def run(
    argv: list[str],
    *,
    check: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run one privileged command from an argument array.

    ``argv[0]`` must be one of :data:`ALLOWED_EXECUTABLES`; it is replaced by
    the absolute path resolved at start-up. Every remaining element is passed
    to the kernel as a separate argument, so none of them can be interpreted
    as shell syntax.
    """
    if not argv:
        raise ValidationError("An empty command cannot be run.")
    resolved = [executable(argv[0]), *(str(part) for part in argv[1:])]

    started = time.perf_counter()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed path, argument array, no shell
            resolved,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
        result = CommandResult(
            argv=resolved,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
    except subprocess.TimeoutExpired:
        result = CommandResult(
            argv=resolved,
            returncode=124,
            stdout="",
            stderr=f"Command exceeded its {timeout:g} second timeout and was killed.",
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )

    COMMAND_LOG.add(result)
    if not result.ok:
        # A caller passing check=False is saying the command is allowed to
        # fail, for example deleting something that may not exist. Those are
        # recorded in the audit log but are not warnings.
        logger.log(
            logging.WARNING if check else logging.DEBUG,
            "command failed: %s",
            result.structured(),
        )
    if check and not result.ok:
        raise CommandError(result)
    return result


def in_namespace(
    namespace: str,
    argv: list[str],
    *,
    check: bool = True,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CommandResult:
    """Run a command inside one of the laboratory namespaces.

    The namespace is validated against the agent's own constant list, so this
    cannot be pointed at the host's root namespace.
    """
    validate_namespace(namespace)
    return run(["ip", "netns", "exec", namespace, *argv], check=check, timeout=timeout)
