# Plugin Template

> 中文: [README.md](README.md)

A runnable plugin with no third-party dependencies that uses every host
extension point once. The feature is notes plus file attachments.

Ships `enabled: false`. Press Enable on the plugin management page, or set
`enabled: true` in `plugin.yaml`. Falls back to a JSON file when Redis is down.
This directory is in `.gitignore`.

## Copy it to start

```bash
cp -r plugins/plugin-template plugins/my-plugin
```

```bash
cd plugins/my-plugin
uv run python main.py rename my-plugin      # manifest, tools, subagents, commands
uv run python main.py doctor                # confirm nothing was missed
```

`rename` only rewrites names this plugin actually registered, so framework
vocabulary like the `template_list` config type is left alone. After that:

1. Set `version`, `description` and `metadata` in `plugin.yaml`.
2. Replace `tests/test_plugin_*.py` with your plugin's own behaviour tests.
   Leave `tests/test_plugin_contract.py` alone.
3. Delete what you do not need. All four fixed module names are optional.
4. Set `enabled: true` and press Reload on the management page.

## Files

| File | Contents |
| --- | --- |
| `plugin.yaml` | Manifest: metadata, dependencies, log levels, `ui.pages`, `ui.panels` (nine widget types), `config.commands`, `config.tool_access` |
| `requirements.txt` | Dependency format and the forms that are rejected |
| `_conf_schema.json` | All ten field types, `label`/`hint`/`options`/`secret`/`invisible`, `options_source` |
| `SKILL.md` | Skill markdown, discovered by the host; no `register_skills` |
| `hooks.py` | One handler at each of the ten lifecycle points |
| `tools.py` | Three registration styles, dependency injection, `consequential`, input schemas, storage |
| `subagents.py` | Decorated and explicit registration, restricted tool views, `vote` node voting |
| `web.py` | Two endpoint styles, GET/POST/PUT/DELETE, attachment upload/list/download/delete, binaries, SSE, the tool executor |
| `pages/console/` | Sandboxed page using every bridge method, bilingual, light and dark |
| `store.py` | Loading a module that is not one of the fixed names; Redis and file backends; upload name and extension vetting |
| `pyproject.toml` | ruff and pytest settings plus dev dependencies for standalone work; `package = false`, nothing to build |
| `main.py` | Run the plugin without the dashboard: inspect, doctor, call, hook, web, serve, rename |
| `tests/test_plugin_contract.py` | **Generic**: derived from the plugin's own declarations; copy it unchanged |
| `tests/test_plugin_template.py` | This plugin's own behaviour; replace it with yours |
| `tests/conftest.py` | Fixtures and the backend choice |
| `tests/harness/` | The stand-in host and dev tooling, **copied unchanged** (see below) |
| `.github/workflows/` | CI (lint and tests on every push) and Release (tags and publishes when the version changes), **copied unchanged** |
| `.github/scripts/plugin_version.py` | Reads and validates the version in `plugin.yaml` for the release workflow; fails if `pyproject.toml` declares a static version of its own |

Nothing under `tests/harness/` knows about this plugin, so it copies unchanged:

| File | Contents |
| --- | --- |
| `host.py` | `RealHost`, `FakeHost` and `select_host()` behind one interface |
| `shim.py` | Installs `extension.plugin` / `.hook` / `.plugin_web` into `sys.modules` |
| `loader.py` | Manifest, config schema, command and requirements rules for the standalone side |
| `fakes.py` | Stand-in tool registry, hook manager, subagent registry, storage, web context, executor |
| `checks.py` | The generic checks; shared by the tests and `main.py doctor` |
| `pageaudit.py` | Static page checks, plus the endpoints the page's JS calls |
| `report.py` | The capability inventory |
| `devserver.py` | `main.py serve` and its development bridge |
| `rename.py` | `main.py rename` |

No `__init__.py` at the plugin root or in `tests/`. With one, pytest prepends
that directory to `sys.path`, where `tools.py` shadows the project's `tools`
package and test collection fails. The host imports plugin modules by file path
and needs no package marker. `tests/harness/` is the exception: it is imported
as `import harness`, so that level does have one.

## Picking an extension point

| Goal | Use |
| --- | --- |
| Add a capability the model can call | A tool in `tools.py` |
| Add a slash command | `config.commands` in `plugin.yaml` |
| Handle a message before the agent sees it | `before_route` in `hooks.py`, setting `route_outcome` |
| Influence routing without hard-coding it | `router_hints`, or `planner_hints` on the command |
| Refuse one tool call | `context["denied"]` and `context["reason"]` in `before_tool_call` |
| Rewrite every tool result the same way | `after_tool_call` |
| Add an expert the planner can pick | A subagent in `subagents.py` |
| Take part in a `vote` node | Handle `task["_vote_spec"]` in the subagent's `run` |
| Add stat tiles or charts | `ui.panels` plus your own endpoints; no frontend code |
| Build a full interactive page | `pages/<name>/` plus `ui.pages` |
| Store data | `runtime_context["storage"]` |
| Store an uploaded file | Bytes via `storage.dir(...)` plus one index record |
| Let users change settings | `_conf_schema.json` |
| Let users pick a configured model | `options_source: llm.providers` / `llm.models` on the field |

## Commands

A command in `config.commands` is dropped silently — no error, and absent from
`/help` — when any of these hold:

- The name does not start with `/`
- It collides with a core command
- `route` is neither `task` nor `tool`
- `priority` is not an integer
- `route: tool` and `tool` names a tool that is not registered
- `requires_tool` names a tool that is not registered

`priority` bands: 5–20 critical, 21–50 regular, 51+ utility.

## Configuration

`plugin.config = {**the manifest's config, **values saved from
_conf_schema.json}`. Do not declare one key in both places; the schema value
wins. User-editable settings go in the schema, `commands` and `tool_access` in
the manifest.

Field types: `string`, `text`, `int`, `float`, `bool`, `object`, `list`, `dict`,
`template_list`, `file`. `file` and `template_list` values are lists; `object`
and `dict` are objects. Fields accept `label`, `description`, `hint`, `default`,
`options`, `option_labels`, `invisible`, `secret`; `object` declares children
recursively under `items`.

When the choices are runtime state, use `options_source` instead of `options`:

| Value | Effect |
| --- | --- |
| `llm.providers` | Lists every provider this deployment configured, disabled ones included |
| `llm.models` | Lists the models of the provider named by the sibling in `depends_on`; the default provider's models when that field is empty |

The select is clearable and accepts typed input, so an empty value means "use
the host default". It degrades to a text input when the LLM API is unreachable.
Saved values are not re-validated against the live list.

`secret: true` masks the input in the form only; the value is stored in clear
text, so prefer an environment variable for keys. `invisible: true` hides a
field from the form but still puts it in `plugin.config`.

## Storage

`runtime_context["storage"]` is scoped to this plugin; calls never pass a plugin
name:

| Method | Effect |
| --- | --- |
| `storage.dir(*parts)` | A directory private to this plugin, created on demand |
| `storage.path(*parts)` | A private file path, parents created on demand |
| `storage.resolve(path)` | Returns the path when it belongs to this plugin, `None` when it escapes or is missing |
| `storage.user_file(path)` | Resolves a file the host stored for a user, such as a chat attachment; `None` when it escapes |
| `storage.key(*parts)` | A Redis key private to this plugin |
| `storage.client()` | The shared Redis client with retries and health checks; `None` when the backend is down |
| `storage.available()` | Whether the backend is usable right now |

Path segments are sanitised, so `..` cannot leave the plugin's directory. Pass a
caller-supplied path through `resolve` or `user_file` before opening it.

## Uploads and deletion

- The file keeps the name the user uploaded, but the client's string is never
  taken as sent. `safe_stem()` in `store.py` drops the directories, replaces
  every character outside its allow-list, trims leading and trailing dots and
  spaces, bounds the length, and steps around the Windows device names.
- The extension is decided separately and must pass the `ALLOWED_EXTENSIONS`
  allow-list; otherwise it falls back to the declared MIME type, then to `.bin`.
  So `payroll.pdf.exe` lands as `payroll.pdf.pdf`, never as an executable.
- A name already taken becomes `report (1).pdf`, then `report (2).pdf`. The name
  is claimed with an exclusive create, not an `exists()` check, so two uploads
  racing for one name cannot both be told it is free - and the filesystem, not a
  second rule, decides whether `Report.pdf` and `report.pdf` are the same name.
- The download Content-Type comes from the extension, not from the type the
  client claimed.
- Deletion removes the index record and the bytes together — see
  `AttachmentStore.remove()` and `discard_files()`. `actions/prune` uses the
  same path.
- Bound the upload size in the page and in the handler. The page's check is a
  courtesy; the handler's is the one that holds.

## Pages

- Do not write a script tag for `__bridge.js`; the host inserts one.
- Do not put a literal head opening tag inside a comment. The host inserts the
  bridge after the first one it finds in the source text, comments included; hit
  that and the page loads with no script running at all.
- No external resources. The CSP blocks them silently and the static review
  records a finding.
- The page reaches the backend only through `window.CapstonePluginPage`.
- `bridge.upload()` reads the bytes inside the page before crossing. Handing the
  host a `File` fails with `net::ERR_ACCESS_DENIED`, because the host process
  holds no read permission for that path.
- Text rendered from data carries no `data-i18n`, so redraw it on a language
  change — see `paint()` in `app.js`.

## Other notes

- With `enabled: false` nothing is registered, `SKILL.md` included, and the
  management page shows status `disabled`.
- Keep only source in the plugin directory. Runtime data goes under the host's
  `data/`, which Git ignores.
- Declare `description` and `parameters` (a JSON Schema object) on a tool, or
  the planner has to guess.
- Declare `consequential = True` on a tool with real side effects; the approval
  gate reads only that attribute.
- A `before_tool_call` refusal needs both `denied` and `reason`, and the context
  it returns must keep `payload`. `after_tool_call` must keep the `result` key.
- Return a new dict from a hook handler rather than mutating the one passed in.

## main.py: running the plugin without the dashboard

```bash
uv run python main.py inspect                     # what this plugin registers
uv run python main.py doctor                      # the checks, with fixes
uv run python main.py call <tool> '<json>'        # through the executor, hooks and all
uv run python main.py hook <point> '<json>'       # fire one lifecycle hook
uv run python main.py web GET stats               # call an endpoint
uv run python main.py command '/my-plugin-thing'  # route a slash command
uv run python main.py serve                       # open the page in a browser
uv run python main.py rename <new-name>           # rename a copied plugin
```

`serve` starts a standard-library server for `pages/<name>/` and injects a
**development bridge** whose method names match the host's, backed by `fetch` to
this process and routed to the plugin's real handlers. Page code needs no
change, and clicking a button really runs the plugin's Python: uploads,
downloads, binary previews and the event stream are all live.

In the browser console, `window.__setContext({locale:'zh', isDark:true})` flips
language and theme, so you can check the page redraws.

`serve` deliberately does **not** reproduce the sandbox, the CSP or the
authentication. A page that works here can still be blocked in the dashboard by
an off-origin asset; `doctor`'s page checks are what catch that.

## The generic self-checks

`tests/test_plugin_contract.py` and `main.py doctor` run the same checks, from
`tests/harness/checks.py`. Each is derived from the plugin's own declarations,
so changing what the plugin does needs no edit to any of them:

| Check | What it catches |
| --- | --- |
| Every declared command survives collection | A command dropped silently, absent from `/help` |
| `tool_access` keys are registered tools | An allow-list that names nothing and does nothing |
| Every tool declares description, schema and `consequential` | The planner guessing; the approval gate not firing |
| A tool name carries a word from the plugin name | Clashing with a builtin tool name, fatal at startup |
| Subagents declare domain and capabilities; `allowed_tools` exist | The planner can never route to it |
| Hooks sit on valid points, registered under this plugin | Not cleaned up on unload |
| Every declared page has an `index.html` | The page is silently ignored |
| Widget endpoints exist, methods match, `source.field` is returned | An empty panel, or a stat tile showing "—" |
| **Every endpoint the page's JS calls is registered** | Fails only in a browser, in production |
| No external assets, no self-loaded bridge, no head tag in a comment | A blank page |
| Config schema is valid and shares no key with the manifest | The form not rendering; a manifest default silently overridden |
| `requirements.txt` uses no rejected form | An install-time error |
| `SKILL.md` declares name and description | No description in the skill list |
| Every GET endpoint answers instead of raising | A 500 where a 4xx belonged |

A test run ends by printing the capability inventory: tools, subagents, hooks,
skills, commands, endpoints, pages and config. `main.py inspect` prints the
same. Most of the time the useful information is what is *missing* from it.

## Developing it on its own

Copy the directory anywhere and test it without the host source:

```bash
uv sync
uv run pytest
uv run ruff check .
```

The only dependencies are pytest, PyYAML and ruff. `tests/conftest.py` walks up
for `src/extension/plugin.py` and then **actually imports it** before choosing.
Source on disk is not enough: the project's dependencies have to be installed in
whichever environment pytest is running in.

| How you run it | Backend |
| --- | --- |
| `uv run pytest plugins/plugin-template/tests` from the repository root | The real `PluginManager`, registries and tool executor |
| `uv run pytest` inside the plugin directory | Stand-in: the source is above, but the plugin's own environment has none of the project's dependencies |
| A copy outside the repository | Stand-in: no source at all |

Every run prints which backend it used in its header, so falling back to the
stand-in is never silent.

The test bodies are the same either way, so a stand-in that stops matching the
real host fails the real-host run first.

The stand-in installs `extension.plugin`, `extension.hook` and
`extension.plugin_web` into `sys.modules`, so **the plugin's source needs no
change** and the decorators work as usual. It also supplies a tool registry,
hook manager, subagent registry, storage, web context and tool executor.

The only thing the standalone run cannot do is the static review, which needs
the host's AST pass; it skips rather than failing. Page rendering and dependency
version resolution are likewise host-only.

Copy `tests/harness/` along with the plugin.

## Releasing

The host decides whether a plugin has an update by looking at **Git tags**: a
release is a tag named `v<version from plugin.yaml>`. Commits pushed to a branch
are deliberately not a release, so work in progress never reaches users as an
update prompt.

That leaves one thing to do: **bump `version` in `plugin.yaml` and merge to the
default branch.** That is the only place the version is written: `pyproject.toml`
uses `dynamic = ["version"]` and `uv.lock` does not record it, so neither is
touched by a release, and the tag is created for you.

`.github/workflows/release.yml` does the rest — read the version, do nothing if
that tag already exists, otherwise run lint and the test suite, push the tag, and
create the GitHub release (`rc`/`a`/`b` suffixes are marked as prereleases). A
failing test means no tag, which means no user ever sees that version.

Versions must look like `1.2.3`, `1.2.3rc1` or `1.2.3b2`, or the workflow
stops:

```bash
uv run python .github/scripts/plugin_version.py --check
```

On the other side, the dashboard's plugin detail page has a "Check for updates"
button that runs a single `git ls-remote --tags` — no clone — and reports an
update only when a published version is newer than the installed one. Users who
named a tag when installing are pinned to it and are left alone until they
explicitly move.

## Verification inside the host repository

```bash
uv run pytest plugins/plugin-template/tests -q
uv run ruff check plugins/plugin-template --no-respect-gitignore
uv run bandit -r plugins/plugin-template -c ../../pyproject.toml
```

This directory is in `.gitignore`, so a repository-root `ruff check .` skips it;
pass `--no-respect-gitignore` to lint it explicitly.

By hand: set `enabled: true` and restart the backend. The management page shows
status `active`, review `pass`, and a config form with all ten field types. The
sidebar gains Template Console, the panel shows data in all nine widgets, and
`/template-ping` is answered by `before_route` without reaching the agent.

## Further reading

- [Plugin development guide](../../docs/en/guide/plugin-development-guide.md)
- [Plugin frontend guide](../../docs/en/guide/plugin-frontend-guide.md)
- `image-generation-plugin`, a production plugin on the same extension points
