# Releasing agentcore-search-mcp to PyPI

Once published, users install with nothing but `uvx agentcore-search-mcp` (and the
`claude mcp add` command simplifies to `-- uvx agentcore-search-mcp`).

## Status

- **0.1.0 published to PyPI on 2026-07-09** — https://pypi.org/project/agentcore-search-mcp/
- Verified post-publish: `uvx --no-cache agentcore-search-mcp --version` and a live MCP session (`tools/call web_search`) against a real gateway

## Release steps

1. Bump `version` in `pyproject.toml` and `__version__` in `src/agentcore_search_mcp/__init__.py` (keep them equal).
2. Run the quality gates:
   ```bash
   uv run pytest -q && uv run mypy src && uv run ruff check . && uv run black --check .
   ```
3. Build fresh artifacts:
   ```bash
   rm -rf dist && uv build && uvx twine check dist/*
   ```
4. Publish — pick one:

   **Option A — API token (quickest).** Create a token at https://pypi.org/manage/account/token/ (scope it to this project after the first upload), then:
   ```bash
   UV_PUBLISH_TOKEN=pypi-... uv publish
   ```

   **Option B — Trusted publishing (recommended for this repo).** No long-lived token: configure a GitHub Actions *trusted publisher* for the PyPI project (PyPI → project → Publishing), then publish from a workflow using `pypa/gh-action-pypi-publish@release/v1` (or `uv publish --trusted-publishing always`) triggered on a version tag.

5. Verify the published package:
   ```bash
   uvx agentcore-search-mcp@latest --version
   ```

## No-PyPI alternatives

- **uvx from git** (works as soon as this directory is pushed to the public repo; no account needed):
  ```bash
  uvx --from "git+https://github.com/aws-samples/sample-bedrock-api-proxy.git@main#subdirectory=agentcore-search-mcp" agentcore-search-mcp --version
  ```
- **Private index**: publish to AWS CodeArtifact and point uv at it via `UV_INDEX_URL` / `--index`.

## Notes

- License is MIT-0 (matches the repo); classifiers/urls/authors are already set in `pyproject.toml`.
- Publishing under an aws-samples repo is an org-level decision — confirm ownership of the PyPI project name (who holds the token / trusted-publisher config) before the first upload; names on PyPI are first-come-first-served and cannot be transferred easily.
