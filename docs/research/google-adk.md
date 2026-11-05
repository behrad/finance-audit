# Google Agent Development Kit (ADK) for Python: research notes

As of 2026-11-05. Sources: PyPI, the `google/adk-python` source (main @ `0192edd`, 2026-11-05, which is `__version__ = "2.13.0"`), adk.dev (google.github.io/adk-docs now 301-redirects there), and docs.cloud.google.com.
`SRC` = `https://github.com/google/adk-python/blob/main/src/google/adk`. If a claim says UNVERIFIED, no primary source confirmed it.

Naming note: the Vertex AI / Agent Engine product family is now called **Gemini Enterprise Agent Platform**, and Agent Engine is now **Agent Runtime** (https://adk.dev/deploy/agent-runtime/, https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-identity). The API resource is still `reasoningEngines` (same agent-identity page).

---

## 1. Version, Python, install

- Latest: **google-adk 2.13.0**, uploaded 2026-10-23 (changelog date 2026-10-22). Earlier releases: 2.12.0 (10-13), 2.11.0 (10-01), 2.10.0 (09-24). https://pypi.org/pypi/google-adk/json, https://github.com/google/adk-python/blob/main/CHANGELOG.md
- `requires_python >= 3.11` (https://pypi.org/pypi/google-adk/json). The adk.dev 2.0 page still says "Python 3.10 or later" for 1.x (https://adk.dev/2.0/).
- 2.0 went GA for Python on 2026-05-19. It moved ADK to a graph/node execution engine: `BaseAgent` now subclasses `BaseNode`, and overriding `_run_async_impl()` is "silently ignored". https://adk.dev/2.0/
- Install: `pip install google-adk`. Extras you will likely want: `[mcp]` (https://adk.dev/tools-custom/mcp-tools/), `[gcp]` for Model Armor (https://adk.dev/integrations/model-armor/), `[db]` for `DatabaseSessionService` (`SRC/sessions/__init__.py` raises `missing_extra('sqlalchemy','db')`), `[eval]`, `[agent-identity]`. The full extras list is in the PyPI metadata (https://pypi.org/pypi/google-adk/json).

## 2. LlmAgent basics

```python
from google.adk.agents import LlmAgent          # `from google.adk import Agent` also works
from google.genai import types
agent = LlmAgent(name="rules", model="gemini-3.8-flash", instruction=build_instruction,
                 output_schema=Verdict, output_key="verdict",
                 generate_content_config=types.GenerateContentConfig(temperature=0))
```
- **model**: `str | BaseLlm`. If unset, it is inherited from the parent, then falls back to `LlmAgent.set_default_model`. The built-in default is `gemini-3.5-flash` (`SRC/agents/llm_agent.py` L281-298). String IDs are resolved by regex: `gemini-.*` and `projects/.../endpoints/...` go to `Gemini`, `claude-.*` to `Claude`, and there are also `apigee/`, `ollama/gemma3`, and OpenAI (`SRC/models/__init__.py`).
- **Other providers**: `from google.adk.models import LiteLlm`, then `LiteLlm(model="vertex_ai/claude-...")` or any LiteLLM ID. Set the provider env vars first (`SRC/models/lite_llm.py` L4541-4558, https://adk.dev/agents/models/litellm/). Native `Claude`, `OpenAILlm`, `FallbackModel` and `Gemini` classes are also exported (`SRC/models/__init__.py`).
- **instruction**: `str | InstructionProvider`, where `InstructionProvider = Callable[[ReadonlyContext], str | Awaitable[str]]` (`SRC/flows/llm_flows/prompt/_instructions_utils.py` L40).
  - String templates support `{var}` (state), `{var?}` (optional) and `{artifact.name}` (https://adk.dev/agents/llm-agents/).
  - **If you pass a function, state injection is bypassed.** Call `inject_session_state(template, ctx)` yourself if you want it. It has an optional Jinja2 mode (`use_jinja2=True`) (`SRC/agents/llm_agent.py` L838-862, `SRC/utils/instructions_utils.py`). This matters here: a markdown SKILL.md containing `{` would break the plain-string templating, so return it from a provider.
  - `static_instruction` (`types.ContentUnion`) is sent literally as the system instruction, for context caching. When it is set, `instruction` moves into user content (`SRC/agents/llm_agent.py` L311-345).
  - `global_instruction` is deprecated in favour of `GlobalInstructionPlugin` (same file).
- **output_schema**: a pydantic model, `list[Model]`, `list[primitive]`, a dict, or a `Schema`. The final text is validated with `validate_schema`. With `output_key`, the parsed result is stored in state (`SRC/agents/llm_agent.py` L447-470, L1145-1151).
  - **With tools**: the source docstring says ADK "supports using `output_schema` and `tools` together", exposing tools during the loop and enforcing structure only on the final output (L458).
  - The docs add that this works natively only on specific models. Others fall back to a `set_model_response` tool "which may not work reliably" (https://adk.dev/agents/llm-agents/).
  - Recommendation: keep the extraction/rules agents tool-less with output_schema.
- **generate_content_config**: `types.GenerateContentConfig` for temperature, max_output_tokens, safety, thinking and so on. ADK raises `ValueError` if you put `tools`, `system_instruction`, `response_schema` or `http_options.base_url` there. Use the agent fields instead (`SRC/agents/llm_agent.py` L1320-1340).
- Callbacks on LlmAgent: `before_model_callback`, `after_model_callback`, `on_model_error_callback`, `before_tool_callback`, `after_tool_callback`, `on_tool_error_callback` (L492-581). `include_contents='none'` makes the agent stateless per turn (L435).

## 3. Skills: yes, first-class (experimental)

- Docs: "experimental", available since Python ADK v1.25.0 (https://adk.dev/skills/).
- API (`SRC/skills/__init__.py`):
  - Loaders: `load_skill_from_dir`, `load_skills_from_dir`, `load_skill_from_gcs_dir(bucket_name, skill_id, skills_base_path="", project_id=None, credentials=None)`, plus `*_async` variants.
  - Listers: `list_skills_in_dir`, `list_skills_in_gcs_dir`.
  - Models: `Skill`, `Frontmatter`, `Resources`, `Script`, and the `SkillRegistry` ABC.
  - There is also a zip-bytes loader, `_load_skill_from_zip_bytes` (private, `SRC/skills/_utils.py` L369).
- `Skill(frontmatter=Frontmatter(name=..., description=...), instructions="<SKILL.md body>", resources=Resources(references={...}))` follows a three-level model: L1 is the frontmatter, L2 is the body, L3 is the resources (`SRC/skills/models.py` L211-235).
  - Validation: `name` is kebab-case, at most 64 characters, and must match the directory name. `description` is 1-1024 characters (https://adk.dev/skills/).
- **No public "parse SKILL.md from a string" function.** `_parse_skill_md_content(content)` exists but is private (`SRC/skills/_utils.py` L172). From a string (for example, from a DB), build `Skill(...)` yourself, or write it to a temp dir and call `load_skill_from_dir`.
- `SkillToolset(skills=[...], registry=None, code_executor=None, additional_tools=None, tool_filter=None, discovery_mode=SkillDiscoveryMode.LAZY, lifecycle_config=None, ...)` goes in `LlmAgent(tools=[...])` (`SRC/tools/skill_toolset.py` L2128-2170).
  - Discovery: `LAZY` means the model calls `list_skills` and then `load_skill`. `EAGER` injects the catalog XML into the system instruction (L232-247).
  - Programmatic `await toolset.load_skill(tool_context, name)` / `unload_skill` / `list_active_skills`, and `clone_with_updated_skills(skills)` (L2452-2740; changelog 2.10.0).
  - Lifecycle/ephemeral modes are behind `ADK_ENABLE_SKILL_LIFECYCLE=1` (CHANGELOG 2.10.0).
  - `run_skill_script` is offered only if a code executor is configured (CHANGELOG 2.9.0).
  - A GCP skill registry exists: `GCPSkillRegistry` (CHANGELOG 2.11/2.12; https://adk.dev/integrations/skills-registry/).
- **Fit for us**: Skills are *model-driven progressive disclosure*. The model decides whether to load a skill. For a mandatory per-customer finance ruleset that must *always* apply, deterministic injection is safer. Two options:
  - Put the tenant's SKILL.md body into the rules agent's instruction via an `InstructionProvider`, reading `ctx.state["tenant_rules"]` or a tenant registry.
  - Or use `SkillToolset(discovery_mode=EAGER)` and activate the skill programmatically.
  - Assessment (not a documented claim): use the SKILL.md *file format* and `load_skill_from_dir` / `load_skill_from_gcs_dir` for storage and parsing, then inject `skill.instructions` (plus references) yourself. Adopt `SkillToolset` only if customers need multiple optional skills or scripts.

## 4. Running from your own FastAPI code

- `Runner(app=App(name=..., root_agent=agent, plugins=[...]), session_service=..., artifact_service=None, memory_service=None, auto_create_session=False)`. Exactly one of `app`, `agent` or `node` is accepted, and `app` is recommended (`SRC/runners.py` L251-275). `InMemoryRunner(agent, app_name=...)` bundles in-memory services (L2330-2360).
- `runner.run_async(*, user_id, session_id, new_message: types.Content, state_delta=None, run_config=None, ...) -> AsyncGenerator[Event]` (L1124). `state_delta` is a clean way to pass tenant id and rules per invocation.
- Session services (`SRC/sessions/__init__.py`):
  - `InMemorySessionService`.
  - `DatabaseSessionService(db_url)`, which needs an async driver, e.g. `sqlite+aiosqlite:///...` (https://adk.dev/deploy/cloud-run/), and the `[db]` extra.
  - `VertexAiSessionService(project, location, agent_engine_id)` (`SRC/sessions/vertex_ai_session_service.py` L157).
  - A Firestore session service also exists (https://adk.dev/integrations/firestore-session-service/).
  - For a stateless poll-and-process job, InMemory with one fresh session per expense is enough.
- Multimodal input:
```python
msg = types.Content(role="user", parts=[
    types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
    types.Part.from_bytes(data=jpg_bytes, mime_type="image/jpeg"),
    types.Part(text=json.dumps(payhawk_fields))])
async for ev in runner.run_async(user_id=tenant, session_id=sid, new_message=msg): pass
result = (await svc.get_session(app_name=..., user_id=tenant, session_id=sid)).state["verdict"]
```
  `Part.from_bytes` is google-genai API. UNVERIFIED against a specific ADK doc page, but it is the type ADK's `new_message` takes (`SRC/runners.py` L1124). `output_key` with `output_schema` stores the parsed dict in state (https://adk.dev/agents/llm-agents/). Alternatively, read `ev.is_final_response()` and its content. Supported MIME types: `application/pdf`, `text/plain`, `image/png|jpeg|webp` (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/document-understanding).
- `get_fast_api_app(*, agents_dir, agent_loader=None, session_service_uri=None, artifact_service_uri=None, web: bool, url_prefix=None, lifespan=None, extra_plugins=None, auto_create_session=False, trigger_sources=None, ...) -> FastAPI` (`SRC/cli/fast_api.py` L107-146). It serves ADK's own REST API for agents discovered in a directory.
  - Mounting it into an existing app with `app.mount("/adk", adk_app)` is standard Starlette. UNVERIFIED as a documented ADK pattern; the doc pattern is to *add your routes to the returned app* (https://adk.dev/deploy/cloud-run/).
  - For our service, **don't use it**. Call `Runner` directly from our own endpoints and poller.

## 5. Multi-agent patterns

- `SequentialAgent`, `ParallelAgent` and `LoopAgent` still work in Python 2.13 but are **`@deprecated` "in favor of Workflow"**, with the note "Workflow cannot yet be used as an LlmAgent sub-agent" (`SRC/agents/sequential_agent.py` L86-90, `parallel_agent.py` L172, `loop_agent.py` L58). The docs still show them, with `output_key` to `{placeholder}` data passing (https://adk.dev/workflows/patterns/).
- New API: `from google.adk import Workflow`, with `Workflow(name=..., edges=[("START", extract_agent, compare_fn, rules_agent)])`.
  - Agents and plain Python functions are both nodes, and each node's output is passed as `node_input` to the next node (https://adk.dev/graphs/; `SRC/workflow/__init__.py` exports `Workflow`, `FunctionNode`, `JoinNode`, `Edge`, `RetryConfig`, `START`).
  - Fan-out and join are covered at https://adk.dev/graphs/routes/; I did not read that page in detail, so treat them as UNVERIFIED.
  - Fit: extract (LLM) -> compare (deterministic Python function node, no LLM) -> rules (LLM with the tenant skill).
- Callbacks: `before_model_callback` can return an `LlmResponse` to short-circuit. `before_tool_callback` and `after_tool_callback` can block a tool call or rewrite its result (https://adk.dev/callbacks/types-of-callbacks/; field list in `SRC/agents/llm_agent.py` L492-581).
- Plugins (`BasePlugin`) are global hooks registered on `App(plugins=[...])`; passing `plugins` to `Runner` is deprecated.
  - Hooks: `on_user_message`, `before_run`, `before/after_agent`, `before/after_model`, `on_model_error`, `before/after_tool`, `on_tool_error`, `on_event`, `after_run`.
  - Plugin callbacks take precedence over agent callbacks, and a non-None return skips the rest (https://adk.dev/plugins/).
  - Plugins are the right place for cross-tenant guardrails and audit logging.

## 6. Tools

- **FunctionTool**: a plain function in `tools=[fn]` is wrapped automatically. Use the docstring and type hints, and `ToolContext` for state (https://adk.dev/tools-custom/function-tools/).
- **McpToolset** (`[mcp]` extra): `from google.adk.tools.mcp_tool import McpToolset`; `from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams, SseConnectionParams` (https://adk.dev/tools-custom/mcp-tools/).
```python
McpToolset(connection_params=StreamableHTTPConnectionParams(url="https://x/mcp",
           headers={"Authorization": f"Bearer {tok}"}, timeout=5, sse_read_timeout=300),
           tool_filter=["get_expense"],
           header_provider=lambda ctx: {"X-Tenant": ctx.state["tenant_id"]})
```
  - `header_provider: Callable[[ReadonlyContext], dict | Awaitable[dict]]` gives per-request (per-tenant) headers, and the tool-list cache is keyed per identity. Also available: `auth_scheme`/`auth_credential`, `require_confirmation`, `tool_name_prefix` (`SRC/tools/mcp_tool/mcp_toolset.py` L151-269).
  - `MCPToolset` (capitalised) is deprecated (L725).
- **OpenAPIToolset**: `OpenAPIToolset(spec_str=..., spec_str_type="json"|"yaml", auth_scheme=..., auth_credential=..., tool_filter=..., header_provider=...)` creates one `RestApiTool` per operation (`SRC/tools/openapi_tool/openapi_spec_parser/openapi_toolset.py` L64-97; https://adk.dev/tools-custom/openapi-tools/).
  - Could wrap the Payhawk API if it publishes a spec. Whether it does is UNVERIFIED.

## 7. Governance per agent

- **Agent Runtime (formerly Agent Engine)**: a managed, paid runtime. Deploy with `adk deploy agent_engine` (the command name in `SRC/cli/cli_tools_click.py` L2928) or `agents-cli deploy` (https://adk.dev/deploy/agent-runtime/).
- **Agent Identity on Agent Runtime**:
  - Enable it with `identity_type=AGENT_IDENTITY` (the `v1beta1` API), or with `.agent_engine_config.json` `{"identity_type":"AGENT_IDENTITY"}` before `adk deploy`, or with `agents-cli deploy --agent-identity`.
  - Each agent gets a SPIFFE-based principal, `principal://agents.global.org-ORG.system.id.goog/resources/aiplatform/projects/N/locations/L/reasoningEngines/ID`.
  - Grant roles to it with normal IAM allow/deny policies; `principalSet://` covers all agents in a project. Default roles are `aiplatform.agentContextEditor` and `agentDefaultAccess`.
  - Caveats: deleting and recreating an agent gives it a **new principal**, and agent identities can't hold legacy GCS bucket roles. Without the flag, a service account is used (https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/runtime/agent-identity).
  - The GA/Preview status of the feature isn't stated there (UNVERIFIED).
  - Principal Access Boundary policies are recommended for limiting access (search snippet of https://docs.cloud.google.com/agent-registry/identity-and-identifiers; not read in full, UNVERIFIED).
- Related: Agent Gateway (https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview) and IAM policies for agents (https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/policies/iam-overview). Not read; UNVERIFIED details.
- **ADK "Agent Identity Auth Manager"** (Preview, Python >= 1.30.0, `[agent-identity]`):
  - Setup: `CredentialManager.register_auth_provider(GcpAuthProvider())`, then `GcpAuthProviderScheme` as a tool's `auth_scheme`.
  - The caller needs `agentidentity.user` (https://adk.dev/integrations/agent-identity/).
  - It manages outbound OAuth credentials for tools; it is not workload identity.
- **Agent Identity on Cloud Run**: no source found. UNVERIFIED and probably not available. Cloud Run's equivalent is one service account *per Cloud Run service*.
- **Model Armor** (ADK >= 2.8.0, `[gcp]`):
  - Setup: `App(plugins=[ModelArmorPlugin(config=ModelArmorConfig(prompt_template_name="projects/.../templates/...", response_template_name=...))])`.
  - It screens user input and model output, but **not tool results**.
  - `block_on_screening_failure=True` by default, and blocked turns set `custom_metadata['model_armor_blocked']` (https://adk.dev/integrations/model-armor/).
- **Realistic per-customer isolation** (assessment):
  - (a) **Soft isolation, one Cloud Run service.** Per-tenant config (skill, model, allowed tools) and per-tenant credentials go through `header_provider`/state. Use a plugin to enforce policy and log; use Model Armor for content. Cheapest, and enough for most rules.
  - (b) **Hard isolation per tenant.** Deploy one Cloud Run service (own service account, own Secret Manager secrets, own GCS skill bucket) or one Agent Runtime instance (own agent-identity principal) per customer. This gives real IAM boundaries at an ops cost.
  - Start with (a), keeping the tenant boundary at the config/credential layer, so (b) stays a deployment change.

## 8. Deployment

- `adk deploy cloud_run --project=$P --region=$R [--service_name --app_name --with_ui --session_service_uri --artifact_service_uri --port] $AGENT_PATH [-- gcloud flags e.g. --no-allow-unauthenticated]`.
  - It uses Cloud Build: grant `roles/cloudbuild.builds.builder`, plus `roles/secretmanager.secretAccessor` for the API key.
  - In-memory sessions are lost on instance recycle (https://adk.dev/deploy/cloud-run/).
- Dockerfile approach: `FROM python:3.13-slim`, `CMD uvicorn main:app --host 0.0.0.0 --port $PORT`, then `gcloud run deploy --source .` (same page). We should use this, with our own `main.py`.
- Env vars:
  - **Vertex/Agent Platform backend**: `GOOGLE_GENAI_USE_ENTERPRISE=True` plus `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`, using ADC / the service account (https://adk.dev/deploy/cloud-run/).
  - **`GOOGLE_GENAI_USE_VERTEXAI` still works but logs "deprecated, please use GOOGLE_GENAI_USE_ENTERPRISE"** (`SRC/utils/_gcp_metadata.py` L38-139).
  - **AI Studio backend**: `GOOGLE_API_KEY` (same page, via Secret Manager).
- Models (Gemini API list, https://aistudio.google.com/docs/models):
  - `gemini-3.8-flash` is new and is recommended alongside `gemini-3.5-flash-lite`.
  - `gemini-3.6-flash` is stable. `gemini-3.1-pro-preview` is the Pro model, status not stated.
  - `gemini-2.5-*` is deprecated and reroutes on 2026-12-01. The AI Studio page says `gemini-3.5-flash` routes to 3.6 (search snippet of https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash; UNVERIFIED in full).
  - Note that ADK's built-in default is still `gemini-3.5-flash` (§2).
- On Agent Platform, `gemini-3.8-flash` is **GA** (released 2026-09-02, retires 2027-09-02 or later), with a 1,048,576-token context, 65,536 output tokens, thinking LOW/MEDIUM/HIGH (no MINIMAL), and global/US/EU endpoints. Intro price is $0.75/$3.75 per 1M tokens until 2026-12-31 (search summary of https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/3-8-flash; page not fetched directly, so verify the numbers).
- PDF/image limits for Gemini 3+ on Agent Platform: up to 3,000 PDFs and images per prompt, 3,000 pages per file, 256 MB inline part, 2 GiB-500 KiB via GCS, 15 MiB via public URL, 500 MiB total request payload. PDF resolution is 280/560 (default)/1,120 tokens per page, and OCR is off by default (https://docs.cloud.google.com/vertex-ai/generative-ai/docs/multimodal/document-understanding).
  - The Gemini API (AI Studio) docs give **50 MB / 1,000 pages** for PDFs (search snippet of https://ai.google.dev/gemini-api/docs/document-processing; UNVERIFIED in full).
  - Receipts are far below either limit.

## 9. Evaluation

- `adk eval <agent_module_dir> <file.evalset.json>[:eval_1,eval_2] [--config_file_path=test_config.json] [--print_detailed_results]` runs from the CLI. In pytest, use `await AgentEvaluator.evaluate(agent_module, eval_dataset_file_path_or_dir, num_runs=..., ...)`, which accepts a module path, a `BaseNode` or an `App` (https://adk.dev/evaluate/; `SRC/evaluation/agent_evaluator.py` L322-334). Install `[eval]` (PyPI extras).
- `*.test.json` files are single-session unit tests. Evalsets are multi-session.
- Criteria:
  - Defaults: `tool_trajectory_avg_score` 1.0 and `response_match_score` 0.8 (ROUGE-1).
  - Others: `final_response_match_v2` (LLM judge), `rubric_based_final_response_quality_v1`, and `rubric_based_tool_use_quality_v1`.
  - Efficiency metrics are reported but never fail. LLM-judged metrics call the Vertex Gen AI Evaluation Service (https://adk.dev/evaluate/).
- Assessment for regression-testing rules: ROUGE and LLM-judge criteria are a poor fit for an exact verdict JSON. A plain pytest that runs the `Runner` over mock receipts and asserts the parsed `output_schema` fields is simpler and deterministic. Use `adk eval` rubrics only for free-text explanations.

---

## Recommended shape (assessment)

```python
def build_rules_agent(t: TenantConfig) -> LlmAgent:          # cache per (tenant, version)
    skill = load_skill_from_gcs_dir(bucket, t.skill_id)        # or load_skill_from_dir
    return LlmAgent(name="rules", model=t.model,               # "gemini-3.8-flash" or LiteLlm(...)
                    instruction=lambda ctx: BASE + skill.instructions,  # provider: no {} templating
                    output_schema=Verdict, output_key="verdict", include_contents="none")
```
Wrap it in `App(root_agent=Workflow(edges=[("START", extract, compare_fn, rules)]), plugins=[audit, ModelArmorPlugin(...)])` and run one `Runner` per tenant config.
