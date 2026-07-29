"""Tests for CDK deploy script environment validation.

These run the script under /bin/bash on purpose: macOS ships bash 3.2, so any
bash-4-only syntax (e.g. ``${VAR,,}``) aborts the script with "bad substitution"
before it can validate anything — while still exiting non-zero, which makes the
breakage easy to mistake for a working guard.
"""

import os
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_deploy(env_local=os.devnull, **env_overrides):
    """Invoke deploy.sh with a PATH that guarantees it cannot reach AWS.

    PATH is stubbed out so the script fails at the prerequisite check if it ever
    gets that far; every assertion here is about validation that must happen
    *before* prerequisites. Because PATH is unusable, this also proves the
    pre-flight logic relies only on shell builtins.

    env_local defaults to os.devnull so these tests never pick up a real
    cdk/.env.local from the developer's checkout.
    """
    env = os.environ.copy()
    env["PATH"] = "/nonexistent"
    env["CDK_ENV_LOCAL_FILE"] = str(env_local)
    for key in ("BEDROCK_API_KEY", "OPENAI_API_KEY",
                "ENABLE_OPENAI_COMPAT", "ENABLE_OPENAI_PASSTHROUGH"):
        env.pop(key, None)
    env.update(env_overrides)

    return subprocess.run(
        ["/bin/bash", "cdk/scripts/deploy.sh", "-e", "prod", "-s"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


@pytest.mark.parametrize(
    "flag,expected_message",
    [
        ("ENABLE_OPENAI_COMPAT",
         "BEDROCK_API_KEY is required when ENABLE_OPENAI_COMPAT=true"),
        # Passthrough was omitted from the original guard, so prod ran with the
        # feature enabled and no credentials for weeks.
        ("ENABLE_OPENAI_PASSTHROUGH",
         "BEDROCK_API_KEY is required when ENABLE_OPENAI_PASSTHROUGH=true"),
    ],
)
def test_deploy_requires_bedrock_api_key(flag, expected_message):
    result = run_deploy(**{flag: "true"})
    output = result.stdout + result.stderr

    assert result.returncode == 1, output
    assert expected_message in output
    # Must fail before doing any real work.
    assert "Checking prerequisites" not in output


def test_validation_survives_bash_3_2():
    """The guard must not abort on bash-4-only parameter expansion."""
    result = run_deploy(ENABLE_OPENAI_COMPAT="true")
    output = result.stdout + result.stderr

    assert "bad substitution" not in output, (
        f"deploy.sh used bash-4-only syntax and crashed instead of "
        f"validating:\n{output}"
    )


def test_flag_value_is_case_insensitive():
    """TRUE/True/true must all trigger validation."""
    for value in ("TRUE", "True", "true"):
        result = run_deploy(ENABLE_OPENAI_COMPAT=value)
        output = result.stdout + result.stderr
        assert "BEDROCK_API_KEY is required" in output, (
            f"ENABLE_OPENAI_COMPAT={value} did not trigger validation:\n{output}"
        )


def test_no_validation_error_when_key_present():
    """With the key supplied, the Mantle guard must not fire."""
    result = run_deploy(ENABLE_OPENAI_PASSTHROUGH="true",
                        BEDROCK_API_KEY="sk-test-key")
    output = result.stdout + result.stderr

    assert "BEDROCK_API_KEY is required" not in output
    # It should now progress to the prerequisite check (which fails on the
    # stubbed PATH — that is the expected stopping point, not a validation bug).
    assert "Checking prerequisites" in output


def test_script_parses_under_bash_3_2():
    """Catch syntax errors that only surface on the macOS system bash."""
    result = subprocess.run(
        ["/bin/bash", "-n", "cdk/scripts/deploy.sh"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class TestEnvLocalLoader:
    """cdk/.env.local supplies deploy secrets without exporting them by hand.

    The loader must work with only shell builtins: it runs before the
    prerequisite check, and these tests stub PATH to /nonexistent, so any
    reliance on sed/tr/basename shows up here as a "command not found".
    """

    def test_secret_from_file_satisfies_validation(self, tmp_path):
        env_file = tmp_path / ".env.local"
        env_file.write_text("BEDROCK_API_KEY=key-from-file\n")

        result = run_deploy(env_local=env_file, ENABLE_OPENAI_PASSTHROUGH="true")
        output = result.stdout + result.stderr

        assert "Loaded deploy secrets" in output
        assert "BEDROCK_API_KEY is required" not in output
        assert "command not found" not in output

    def test_explicit_env_overrides_file(self, tmp_path):
        """An inline `KEY=... ./deploy.sh` must beat the file's value."""
        env_file = tmp_path / ".env.local"
        env_file.write_text("BEDROCK_API_KEY=key-from-file\n")

        result = run_deploy(env_local=env_file,
                            ENABLE_OPENAI_PASSTHROUGH="true",
                            BEDROCK_API_KEY="key-from-env")
        output = result.stdout + result.stderr

        assert "BEDROCK_API_KEY is required" not in output
        assert "command not found" not in output

    def test_comments_blanks_and_placeholders_ignored(self, tmp_path):
        """A commented-out or empty assignment must not count as a value."""
        env_file = tmp_path / ".env.local"
        env_file.write_text(
            "# BEDROCK_API_KEY=commented-out\n"
            "\n"
            "BEDROCK_API_KEY=\n"          # placeholder, as in the .example file
        )

        result = run_deploy(env_local=env_file, ENABLE_OPENAI_PASSTHROUGH="true")
        output = result.stdout + result.stderr

        assert "BEDROCK_API_KEY is required" in output, output

    def test_quoted_and_spaced_values(self, tmp_path):
        env_file = tmp_path / ".env.local"
        env_file.write_text('  BEDROCK_API_KEY = "quoted-key"  \n')

        result = run_deploy(env_local=env_file, ENABLE_OPENAI_PASSTHROUGH="true")
        output = result.stdout + result.stderr

        assert "BEDROCK_API_KEY is required" not in output, output
        assert "command not found" not in output

    def test_missing_file_is_not_an_error(self, tmp_path):
        result = run_deploy(env_local=tmp_path / "does-not-exist")
        output = result.stdout + result.stderr

        assert "Loaded deploy secrets" not in output
        assert "No such file" not in output


def test_env_local_is_gitignored():
    """The real secrets file must never be committable.

    `.gitignore` already had a bare `.env` rule, which does NOT cover
    `.env.local` — gitignore matches whole names, not prefixes.
    """
    check = subprocess.run(
        ["git", "check-ignore", "cdk/.env.local"],
        cwd=REPO_ROOT, text=True, capture_output=True, timeout=10, check=False,
    )
    assert check.returncode == 0, (
        "cdk/.env.local is not gitignored — deploy secrets could be committed"
    )


def test_env_local_example_is_committable():
    """The template must stay tracked so required vars are discoverable."""
    check = subprocess.run(
        ["git", "status", "--porcelain", "--ignored", "cdk/.env.local.example"],
        cwd=REPO_ROOT, text=True, capture_output=True, timeout=10, check=False,
    )
    # '!!' is git's marker for an ignored path.
    assert not check.stdout.startswith("!!"), (
        f"cdk/.env.local.example is ignored: {check.stdout!r}"
    )
