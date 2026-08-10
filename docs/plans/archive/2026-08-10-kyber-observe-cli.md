# `kyber-observe` CLI — installer for plugins, status bars, and hooks

**Status:** Done
**Date:** 2026-08-10
**Goal:** Ship a `kyber-observe` CLI that installs the project's telemetry collectors (Gemini/AGY status bar + plugin/hooks, Pi extension, Pi ObservMe config) into user harness configurations, replacing the ad-hoc `collectors/gemini/install.sh`.
**Closed:** 2026-08-10 — all §9 acceptance gates green: T1–T13 implemented and code-review APPROVED; T14 empirical verification passed all 11 checklist items; `pytest tests/` → **42 passed** (incl. `test_kyber_observe_{manifest,backup,gemini,pi,cli}`); `pip install -e .`, `kyber-observe --help`, and `python3 -m kyber_observe` all run; `kyber-observe list` enumerates `gemini` and `pi`; install/uninstall/restore, manifest + backup, and `--dry-run` verified in sandbox. Schema version untouched (installer-only, D11).

---

## 1. Problem / Motivation

Today only the Gemini status-line collector has an installer — a 61-line bash
script (`collectors/gemini/install.sh`) that copies one file and hand-merges one
JSON key. The rest of the install surface is manual and undocumented:

- The **AGY OTel plugin + hooks** (`collectors/gemini/agy-otel-telemetry/`,
  with `plugin.json` + `hooks.json` + `telemetry.py`) has *no* installer at all;
  its README literally says *"Deployment and registration are intentionally
  deferred … the eventual installer/packager should deploy it into an AGY
  customization root."*
- The **Pi extension** (`collectors/pi/`) README tells users to copy to
  `~/.pi/agent/extensions/` or run `pi install npm:@dpalfery/pi-statusline`, with
  no automation.
- The **Pi ObservMe yaml** (`.pi/observme.yaml` template) must be hand-edited
  into `~/.pi/agent/observme.yaml`.

There is no idempotency, no backup, no status/uninstall, and no single command
surface across harnesses. As more harnesses are added (OpenCode is already named
in `AGENTS.md`), the lack of a uniform installer becomes the bottleneck.

This plan introduces `kyber-observe`, a Python CLI that owns all collector
installation. It is **installer-only**: it does not touch `canonical.py`,
adapters, `store.py`, or `pipeline.py`, so `SCHEMA_VERSION` is **unchanged**
(stated explicitly per AGENTS.md guideline #1).

## 2. Approved decisions

- **D1 — Framework: typer + `pyproject.toml` console script.** Build the CLI
  with [typer](https://typer.tiangolo.com/) (builds on click; type-hinted,
  matching the project's typing style) and expose it via a PEP 621
  `pyproject.toml` with `[project.scripts] kyber-observe = "kyber_observe.cli:main"`.
  This is the project's first departure from "scripts only" and adds one
  dependency (`typer`) plus a one-time `pip install -e .`. Approved by the user.
- **D2 — New top-level package `kyber_observe/`**, sibling to `agentdash/`.
  Invocation: `kyber-observe …` (after install) or `python3 -m kyber_observe …`.
  No root shim (`kyber-observe.py`); the console script + module form suffice.
- **D3 — Gemini statusline install** = copy `collectors/gemini/statusline.py`
  → `~/.gemini/antigravity-cli/statusline.py` (chmod +x) and merge a
  `statusLine` block into `~/.gemini/antigravity-cli/settings.json`,
  **preserving all existing keys** (notably `mcpServers`). Verified against the
  official Antigravity statusline doc: `statusLine` accepts `type`, `command`,
  `enabled`, `padding`, `stack_with_default`. Matches the current `install.sh`.
- **D4 — Gemini plugin/hooks install** = deploy the whole
  `collectors/gemini/agy-otel-telemetry/` directory (as a unit, because
  `hooks.json` uses relative `./telemetry.py`) to
  `~/.gemini/antigravity-cli/plugins/agy-otel-telemetry/`. Verified path from
  the official Antigravity plugin doc. Two methods:
  `--method copy` (direct `copytree`) or `--method agy` (delegate to
  `agy plugin install <staged_path>`, the native subcommand the docs define).
- **D5 — Pi extension install**, two methods per user choice:
  `--method copy` → copy `collectors/pi/` to
  `~/.pi/agent/extensions/pi-statusline/` (excluding `node_modules`,
  `__pycache__`, `.gitignore`); `--method pi-install` → run
  `pi install npm:@dpalfery/pi-statusline`. Default `copy` (reliable offline).
- **D6 — Pi ObservMe yaml install** (component `observme`) = render
  `~/.pi/agent/observme.yaml` from the `.pi/observme.yaml` template with the
  `otlp.endpoint` substituted from `--endpoint` (default
  `http://localhost:4318`).
- **D7 — Command surface.** `kyber-observe install <harness> [--component ...]
  [--method ...] [--endpoint URL] [--dry-run] [--force]`,
  `uninstall <harness>`, `status`, `list`; global `--verbose`.
- **D8 — Pluggable `HarnessInstaller` registry.** v1 ships Gemini + Pi
  installers; OpenCode and others drop in later by adding a module under
  `kyber_observe/installers/` and registering, with no CLI changes.
- **D9 — Safety: backup + manifest + idempotency.** Before mutating any user
  config file, copy it to `~/.config/kyber-observe/backups/<timestamp>/`.
  Record every installed component in
  `~/.config/kyber-observe/manifest.json` (schema:
  `{harness, component, method, version, source_path, install_path,
  backup_refs, installed_at}`). Re-install is idempotent (manifest check;
  `--force` re-installs). `uninstall` removes installed files and restores
  backed-up config. `--dry-run` prints the plan and writes nothing. Manifest
  root overridable via `KYBER_OBSERVE_HOME` env var.
- **D10 — All subprocess calls use list args** (`subprocess.run([...])`), never
  `shell=True`, never string interpolation of user input. The installer writes
  into `$HOME` and shells out to `agy`/`pi`; no secrets are handled.
- **D11 — `SCHEMA_VERSION` unchanged.** Installer-only change; no edits to
  `canonical.py`, adapters, `store.py`, or `pipeline.py`.

## 3. Investigation findings

- **Antigravity plugin path (authoritative).** Official docs
  (`https://antigravity.google/docs/cli/plugins`) state plugins are staged at
  `~/.gemini/antigravity-cli/plugins/<plugin_name>/` with layout
  `plugin.json` (required) + optional `mcp_config.json`/`hooks.json`/`skills/`/
  `agents/`/`rules/`. Native subcommands: `agy plugin install <path>`,
  `enable`/`disable <name>`, `uninstall <name>`, `list`. Hooks are defined in
  the plugin's `hooks.json` or in primary `settings.json`; inspect via `/hooks`
  in the TUI.
- **Antigravity statusline path (authoritative).** Docs
  (`/docs/cli/statusline`): `statusLine` block in
  `~/.gemini/antigravity-cli/settings.json` with `type:"command"`, `command`,
  optional `enabled`/`padding`/`stack_with_default`. State JSON is piped to the
  script's stdin.
- **`plugin.json` schema risk.** The official manifest schema is
  `additionalProperties: false` (only `name`, `description`, `$schema`). The
  current `collectors/gemini/agy-otel-telemetry/plugin.json` carries `version`,
  `author`, `license` → may be rejected by `agy plugin install`. Direct
  `--method copy` is unaffected. Task T8 makes it compliant.
- **AGY hook env vars (from `telemetry.py`).** The plugin reads
  `AGY_OTEL_ENDPOINT` (default `http://127.0.0.1:4318/v1/traces`),
  `AGY_OTEL_CAPTURE_CONTENT`, `AGY_OTEL_STATE_DIR`,
  `AGY_OTEL_TIMEOUT` (default 2s) from the hook process environment. AGY docs
  define no plugin-env config, so the installer **cannot** reliably inject
  these into the hook process; `--endpoint` is only writable into the Pi
  `observme.yaml`. For AGY the installer documents the required env vars
  rather than writes them. `hooks.json` commands use relative `./telemetry.py`,
  which resolves correctly once the dir is staged under `plugins/<name>/`.
- **Pi extension loads TS source directly** (`extension.ts` imports sibling
  `.ts`; `package.json` has no `build` script; `pi … -e .` loads the dir). So
  `--method copy` needs no build step.
- **`requirements.txt`** today has exactly one dep (`tiktoken`); the project
  runs scripts from repo root (`serve.py`, `python3 -m agentdash.ingest`) with
  no `pyproject.toml`/`setup.py`. D1 is the first packaging change.
- **No collision** with the Draft plan `2026-08-09-gemini-ingestion-provider.md`
  (that covers the Gemini *adapter*; this covers the *installer*).

## 4. Task list

| # | Phase | Component | Description | Skills |
|---|-------|-----------|-------------|--------|
| 1 | foundation | `pyproject.toml`, `requirements.txt`, `kyber_observe/{__init__,__main__}.py` | Introduce PEP 621 packaging + typer entry point `[project.scripts] kyber-observe = "kyber_observe.cli:main"`; add `typer` to `requirements.txt`; stub package so `pip install -e .` and `kyber-observe --help` work. | python-dev |
| 2 | core | `kyber_observe/config.py`, `kyber_observe/io.py` | Path constants (`~/.gemini/antigravity-cli/`, `…/plugins/`, `~/.pi/agent/`, `~/.config/kyber-observe/`), `DEFAULT_ENDPOINT`, `KYBER_OBSERVE_HOME` override; IO primitives `atomic_write`, `load_json`, `merge_json_preserving` (keeps unknown top-level keys), `copytree_excluding` (skips `node_modules`/`__pycache__`). | python-dev |
| 3 | core | `kyber_observe/backup.py`, `kyber_observe/manifest.py` | `backup_file`/`restore_file` to `~/.config/kyber-observe/backups/<ts>/`; `Manifest` class (read/write/upsert/has/remove) with schema `{harness,component,method,version,source_path,install_path,backup_refs,installed_at}`; idempotent upsert; tolerant of missing file. | python-dev |
| 4 | core | `kyber_observe/installers/__init__.py`, `kyber_observe/installers/base.py` | `HarnessInstaller` ABC (`install`/`uninstall`/`status`/`dry_run`, `components` list); `InstallerContext` dataclass (endpoint, method, component, dry_run, force); `REGISTRY` dict + `get_installer(name)` raising a typed error on unknown names. | python-dev |
| 5 | installer | `kyber_observe/installers/gemini.py` | `GeminiInstaller` with components: `statusline` (copy + chmod +x, backup+merge `statusLine` into settings.json preserving `mcpServers`); `plugin` (deploy `agy-otel-telemetry/` to `…/plugins/agy-otel-telemetry/`; `--method copy` copytree; `--method agy` runs `agy plugin install <path>` via `subprocess.run(list)`). | python-dev |
| 6 | installer | `kyber_observe/installers/pi.py` | `PiInstaller` with components: `extension` (`--method copy` copytree excluding `node_modules` to `~/.pi/agent/extensions/pi-statusline/`; `--method pi-install` runs `pi install npm:@dpalfery/pi-statusline`); `observme` (render `~/.pi/agent/observme.yaml` from `.pi/observme.yaml`, substitute endpoint). | python-dev |
| 7 | cli | `kyber_observe/cli.py` | typer app: `install`/`uninstall`/`status`/`list`, global `--verbose`; wire to registry + manifest + backup; `--dry-run` prints plan, writes nothing. | python-dev |
| 8 | collector fix | `collectors/gemini/agy-otel-telemetry/plugin.json` | Make schema-compliant per official `additionalProperties:false`: add `$schema`, keep `name`+`description`, relocate `version`/`author`/`license` so `agy plugin install` does not reject the manifest. | python-dev |
| 9 | deprecate | `collectors/gemini/install.sh` | Add deprecation banner pointing to `kyber-observe install gemini --component statusline`; keep functional. | python-dev |
| 10 | tests | `tests/test_kyber_observe_manifest.py`, `tests/test_kyber_observe_backup.py` | Manifest CRUD + idempotency + backup_refs; backup/restore round-trip; missing-manifest tolerance. Uses `tmp_path` + monkeypatched `HOME`. | test-dev |
| 11 | tests | `tests/test_kyber_observe_gemini.py` | settings.json merge preserves `mcpServers` + unknown keys; plugin copytree correctness; `--method agy` constructs correct subprocess args (mocked); `--dry-run` writes nothing. | test-dev |
| 12 | tests | `tests/test_kyber_observe_pi.py` | copy excludes `node_modules`; observme endpoint substitution; `pi-install` mocked subprocess. | test-dev |
| 13 | tests | `tests/test_kyber_observe_cli.py` | typer `CliRunner`: `list`/`status`/`install --dry-run`/`uninstall` paths. | test-dev |
| 14 | verify | manual (AGENTS.md §3 "Empirical Verification") | `pip install -e .`; `kyber-observe list`; `install gemini --dry-run`; `install gemini`; verify statusline + plugin staged, settings.json intact, `cat sample.json \| python3 statusline.py` runs; `status`; `uninstall` restores; re-install idempotent; repeat for pi; `pytest tests/`. | python-dev |

## 5. Sequencing / dependency graph

```
1 (packaging) ─► 2 (io/config) ─► 3 (backup/manifest) ─► 4 (registry/base)
                                                       │
                ┌──────────────────────────────────────┼─────────────────┐
                ▼                                      ▼                 ▼
             5 (gemini) ──► 8 (plugin.json)        6 (pi)             7 (cli)
                │                                       │                 │
                └──────────────┬────────────────────────┘                 │
                               ▼                                          │
                    10,11,12 (tests) ─► 13 (cli tests) ─► 9 (deprecate) ─► 14 (verify)
```

- 5 and 6 are disjoint and parallel after 4. 8 follows 5 (validates the
  `--method agy` path).
- 10/11/12 parallel once their installer lands; 13 after 7.
- 14 (verify) is last and depends on everything.

## 6. Residual decisions / risks

- **Packaging convention change (D1).** First `pyproject.toml` and first new
  runtime dep (`typer`); introduces a one-time `pip install -e .`. Approved by
  the user; flagged because it's a visible workflow shift for a repo that has
  been "scripts only".
- **`plugin.json` schema compliance (T8).** Until T8 lands the `--method agy`
  path may fail validation; `--method copy` is unaffected. Risk is contained to
  one small file.
- **`pi-install` requires npm publish.** `pi install npm:@dpalfery/pi-statusline`
  only works once the package is published to npm. Publishing is **out of
  scope**; `--method copy` is the reliable default.
- **AGY hook env vars not writable by installer.** `telemetry.py` reads
  `AGY_OTEL_*` from the hook process env; AGY defines no plugin-env config.
  Installer documents them; only `observme.yaml` accepts `--endpoint`. Verified.
- **Manifest location on macOS.** `~/.config/kyber-observe/` (XDG-style) with
  `KYBER_OBSERVE_HOME` override; not the native `~/Library/Application Support`,
  acceptable on darwin.
- **`SCHEMA_VERSION`: no change** (D11). Installer-only; no canonical/adapter/
  store/pipeline edits.
- **Security.** Installer writes into `$HOME` and shells out to `agy`/`pi`.
  Mitigation: D10 — list-arg subprocess only, no `shell=True`, no interpolation.

## 7. Out of scope

- **OpenCode installer.** No OpenCode collector exists in-tree. The registry
  (D8) makes it a future drop-in.
- **Publishing `@dpalfery/pi-statusline` to npm.** Required only for Pi
  `--method pi-install`; belongs to release/publish tooling.
- **Editing collector behavior** (`statusline.py`, `telemetry.py`, pi
  extension sources). T8 is the sole collector-file change and is narrowly
  scoped to manifest compliance so the installer's `--method agy` path works.
- **Injecting `AGY_OTEL_*` into AGY's hook process env.** Not supported by the
  upstream plugin model; documented, not automated.
- **Dashboard / adapter / store / pipeline changes.** Installer-only.

## 8. Required skills

- **python-dev** — packaging (T1), IO/config (T2), backup/manifest (T3),
  registry/base (T4), both installers (T5, T6), CLI (T7), plugin.json fix (T8),
  install.sh deprecation (T9), and manual empirical verification (T14).
- **test-dev** — manifest/backup tests (T10), gemini installer tests (T11),
  pi installer tests (T12), CLI tests (T13).

(No Azure, data-access, or security-review scope: the change is local-only,
adds no network/secret surface beyond documented outbound OTLP, and all
subprocess calls are list-arg.)

## 9. Verification harness

- **Unit:** `pytest tests/` passes, including the four new test files
  (`test_kyber_observe_manifest.py`, `…_backup.py`, `…_gemini.py`, `…_pi.py`,
  `…_cli.py`).
- **Install:** `pip install -e .` succeeds; `kyber-observe --help` and
  `python3 -m kyber_observe --help` both run; `kyber-observe list` enumerates
  `gemini` and `pi`.
- **Gemini (statusline):** `kyber-observe install gemini --component statusline`
  writes `~/.gemini/antigravity-cli/statusline.py`, leaves `mcpServers` and all
  pre-existing settings.json keys intact, and the collector self-test
  (`cat statusline_last_stdin.json | python3 statusline.py`) produces output.
- **Gemini (plugin):** `… --component plugin` stages
  `~/.gemini/antigravity-cli/plugins/agy-otel-telemetry/` containing
  `plugin.json`/`hooks.json`/`telemetry.py`; `--method agy` invokes
  `agy plugin install <path>` (after T8).
- **Pi:** `kyber-observe install pi --component extension --method copy` copies
  the dir minus `node_modules`; `… --component observme --endpoint <url>`
  writes `~/.pi/agent/observme.yaml` with the substituted endpoint.
- **Safety:** `kyber-observe status` reports installed components + backup
  locations; `kyber-observe uninstall gemini` removes installed files and
  restores the backed-up `settings.json`; re-running `install` is a no-op
  unless `--force`.
- **Dry-run:** `kyber-observe install gemini --dry-run` prints intended actions
  and writes nothing to `$HOME`.
