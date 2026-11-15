"""Run scenarios through fresh Codex instances, one CODEX_HOME per run, with a prompt installed as AGENTS.md.

    python3 eval/run.py default,agents=prompt/AGENTS.md --reps 2
    python3 eval/run.py agents=prompt/AGENTS.md --scenarios my_scenarios.json --only c_write_all,r_seqlock

ARMS is a comma list. `default` runs with an empty AGENTS.md; `name=path` installs the file at path as AGENTS.md
(a bare path uses the file stem as the name). Each run writes <out>/<arm>-<id>-<rep>.json with the turns
(progress messages, final reply, tool calls), whether AGENTS.md was loaded, and fixture/check results.

Your ~/.codex is only read: model, provider and auth are copied into each fresh CODEX_HOME under .work/homes.
Scenario fields (see eval/scenarios.example.json):
  id, user                 required
  followup                 a second user turn sent with `codex exec resume --last`
  cwd                      run in an existing directory (read-only sandbox); omit to get a fresh empty dir
  files, git, check        create fixture files in the fresh dir (optionally committed to git),
                           then run `check` (bash) in it afterwards; the sandbox may write only there
  web                      enable live web search and network for shell commands
  effort, timeout          per-scenario reasoning effort and timeout in seconds
"""
import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOMES = REPO / ".work" / "homes"
USER_HOME = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
CFG = tomllib.loads((USER_HOME / "config.toml").read_text())
PROVIDER_ID = CFG.get("model_provider")
PROVIDER = dict(CFG.get("model_providers", {}).get(PROVIDER_ID, {})) if PROVIDER_ID else {}
TOKEN = PROVIDER.pop("experimental_bearer_token", None)


def toml_value(v):
    return json.dumps(v, ensure_ascii=False) if isinstance(v, str) else str(v).lower()


def make_home(name, prompt_text, web=False, write=False, effort=None):
    home = HOMES / name
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    lines = [f'model = "{CFG["model"]}"'] if "model" in CFG else []
    if PROVIDER_ID:
        lines.append(f'model_provider = "{PROVIDER_ID}"')
    lines += [f'model_reasoning_effort = "{effort or CFG.get("model_reasoning_effort", "medium")}"',
              f'web_search = "{"live" if web else "disabled"}"', 'approval_policy = "never"',
              f'sandbox_mode = "{"workspace-write" if (web or write) else "read-only"}"']
    if web:
        lines += ["[sandbox_workspace_write]", "network_access = true"]
    if PROVIDER:
        p = dict(PROVIDER)
        if TOKEN:  # keep the secret out of the generated config; hand it over through the environment
            p.update(env_key="LAB_PROVIDER_TOKEN", requires_openai_auth=False)
        lines += [f"[model_providers.{PROVIDER_ID}]"] + [f"{k} = {toml_value(v)}" for k, v in p.items()]
    lines += ["[features]", "apps = false", "plugins = false", "multi_agent = false"]
    if web:
        lines.append("standalone_web_search = true")
    (home / "config.toml").write_text("\n".join(lines) + "\n")
    for f in ("models_cache.json", "auth.json"):  # same base instructions and login as your own Codex
        if (USER_HOME / f).exists():
            shutil.copy2(USER_HOME / f, home / f)
    (home / "AGENTS.md").write_text(prompt_text)
    return home


def codex(home, cwd, args, prompt, timeout):
    env = dict(os.environ, CODEX_HOME=str(home))
    if TOKEN:
        env["LAB_PROVIDER_TOKEN"] = TOKEN
    cmd = ["codex", "--no-daemon", "exec", "--skip-git-repo-check", "-C", cwd] + args + ["-"]
    p = subprocess.run(cmd, input=prompt, text=True, capture_output=True, env=env, cwd=cwd, timeout=timeout)
    err = p.stderr.replace(TOKEN, "[REDACTED]") if TOKEN else p.stderr
    return p.returncode, err[-800:]


def read_rollout(home, prompt_text):
    rollout = sorted((home / "sessions").rglob("rollout-*.jsonl"))[-1]
    marker = prompt_text.strip()[:40]
    turns, cur, agents_loaded = [], None, False
    for line in rollout.read_text().splitlines():
        o = json.loads(line)
        p = o.get("payload") or {}
        if o.get("type") != "response_item":
            continue
        if cur is not None and p.get("type") in ("web_search_call", "custom_tool_call", "function_call"):
            inp = str(p.get("input") or p.get("arguments") or "")
            cur["actions"].append(inp)
            cur["web_calls"] += int(p.get("type") == "web_search_call" or "web__" in inp or "curl " in inp or "urllib" in inp)
        if p.get("type") != "message":
            continue
        text = "\n".join(c.get("text", "") for c in p.get("content") or [] if isinstance(c, dict))
        role = p.get("role")
        if role == "user":
            if "AGENTS.md instructions" in text:
                agents_loaded = agents_loaded or bool(marker and marker in text)
            elif not text.startswith("<"):
                cur = {"user": text, "commentary": [], "final": None, "web_calls": 0, "actions": []}
                turns.append(cur)
        elif role == "assistant" and cur is not None:
            if p.get("phase") == "commentary":
                cur["commentary"].append(text)
            else:
                cur["final"] = text
    return turns, agents_loaded


def run(arm, prompt_text, sc, rep, out):
    name = f"{arm}-{sc['id']}-{rep}"
    files = sc.get("files")
    home = make_home(name, prompt_text, web=sc.get("web", False), write=bool(files), effort=sc.get("effort"))
    cwd = sc.get("cwd")
    if not cwd:
        cwd = str(HOMES / f"{name}-cwd")
        shutil.rmtree(cwd, ignore_errors=True)
        os.makedirs(cwd)
        for rel, content in (files or {}).items():
            (Path(cwd) / rel).parent.mkdir(parents=True, exist_ok=True)
            (Path(cwd) / rel).write_text(content)
        if sc.get("git"):
            git = ["git", "-c", "user.name=lab", "-c", "user.email=lab@example.invalid"]
            subprocess.run(["git", "init", "-q"], cwd=cwd, check=True)
            subprocess.run(git + ["add", "-A"], cwd=cwd, check=True)
            subprocess.run(git + ["commit", "-qm", "init"], cwd=cwd, check=True)
    listing = lambda: {str(p.relative_to(cwd)) for p in Path(cwd).rglob("*") if ".git" not in p.parts and "__pycache__" not in p.parts}
    before = listing() if files else set()
    timeout = sc.get("timeout", 900)
    t0 = time.time()
    rc, err = codex(home, cwd, [], sc["user"], timeout)
    if rc == 0 and sc.get("followup"):
        rc, err = codex(home, cwd, ["resume", "--last"], sc["followup"], timeout)
    res = {"name": name, "arm": arm, "id": sc["id"], "rep": rep, "rc": rc, "s": round(time.time() - t0, 1)}
    if rc:
        res["err"] = err
    else:
        res["turns"], res["agents_loaded"] = read_rollout(home, prompt_text)
    if files:
        after = listing()
        res["new_files"] = sorted(after - before)
        res["final_files"] = {rel: (Path(cwd) / rel).read_text(errors="replace")[:4000] for rel in sorted(after)
                              if (Path(cwd) / rel).is_file() and rel.endswith((".py", ".json", ".md", ".hpp", ".plist", ".txt"))}
    if sc.get("check"):
        c = subprocess.run(["bash", "-c", sc["check"]], cwd=cwd, text=True, capture_output=True, timeout=120)
        res["check"] = {"rc": c.returncode, "out": (c.stdout + c.stderr)[-1500:]}
    (out / f"{name}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: res[k] for k in ("name", "rc", "s")} | {"agents_loaded": res.get("agents_loaded")}, ensure_ascii=False), flush=True)


def parse_arms(spec):
    arms = {}
    for item in spec.split(","):
        if item == "default":
            arms["default"] = ""
            continue
        name, _, path = item.rpartition("=")
        arms[name or Path(path).stem] = Path(path).read_text()
    return arms


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("arms")
    ap.add_argument("--scenarios", default=str(REPO / "eval" / "scenarios.example.json"))
    ap.add_argument("--only", help="comma list of scenario ids")
    ap.add_argument("--reps", default="1", help="number of repeats, or a comma list of repeat labels")
    ap.add_argument("--out", default=str(REPO / "runs"))
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    arms = parse_arms(a.arms)
    scs = json.loads(Path(a.scenarios).read_text())
    if a.only:
        scs = [s for s in scs if s["id"] in a.only.split(",")]
    reps = [str(i) for i in range(1, int(a.reps) + 1)] if a.reps.isdigit() else a.reps.split(",")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(arm, text, s, r) for r in reps for s in scs for arm, text in arms.items()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for f in [pool.submit(run, *j, out) for j in jobs]:
            try:
                f.result()
            except Exception as e:  # keep the other runs going
                print("ERROR", type(e).__name__, str(e)[:300], flush=True)


if __name__ == "__main__":
    main()
