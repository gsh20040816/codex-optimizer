"""Index your human turns from Claude Code and Codex transcripts as JSONL on stdout.

    python3 analysis/extract_turns.py > turns.jsonl
    python3 analysis/extract_turns.py --claude-dir ~/.claude/projects --claude-dir /other/config/projects

Each record: group, model, session, cwd, ts, user, final, tools, docs.
  group: claude (Claude models in Claude Code), cc-gpt (non-Claude models served through Claude Code),
         codex (interactive Codex sessions; `codex exec` runs are skipped unless --include-exec)
  final: the last text the assistant wrote in that turn; tools: tool names used in the turn
  docs: full text of the .md/.html files the turn wrote with Write (Claude Code only; memory and prompt files skipped)
Only typed human messages start a turn. Copies of the same session in several directories are merged.
"""
import argparse
import glob
import io
import json
import os
import subprocess

seen = set()


def text_of(c):
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c or [] if isinstance(x, dict) and x.get("type") in ("text", "input_text", "output_text"))


def emit(r):
    if not r or not r["user"].strip() or not r["final"].strip():
        return
    key = (r["session"], r["ts"], r["user"][:80])
    if key in seen:
        return
    seen.add(key)
    r["tools"] = sorted(t for t in r["tools"] if t)
    print(json.dumps(r, ensure_ascii=False))


def claude_file(path):
    r = None
    for line in open(path, errors="replace"):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if o.get("isSidechain"):
            continue
        if o.get("type") == "user":
            c = (o.get("message") or {}).get("content")
            if isinstance(c, list) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                continue
            if o.get("turnOrigin") not in (None, "human") or o.get("promptSource") not in (None, "typed"):
                continue
            t = text_of(c).strip()
            if not t or t.startswith("<"):
                continue
            emit(r)
            r = {"group": "claude", "model": None, "session": o.get("sessionId"), "cwd": o.get("cwd"),
                 "ts": o.get("timestamp"), "user": t, "final": "", "tools": set(), "docs": []}
        elif o.get("type") == "assistant" and r is not None:
            m = o.get("message") or {}
            model = m.get("model") or ""
            if model and not model.startswith("<"):
                r["model"] = model
                r["group"] = "claude" if model.startswith("claude") else "cc-gpt"
            for b in m.get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    r["final"] = b["text"].strip()
                elif b.get("type") == "tool_use":
                    r["tools"].add(b.get("name"))
                    path = (b.get("input") or {}).get("file_path", "")
                    if b.get("name") == "Write" and path.endswith((".md", ".html")) and "/memory/" not in path \
                            and os.path.basename(path) not in ("CLAUDE.md", "AGENTS.md"):
                        r["docs"].append(b["input"].get("content", ""))
    emit(r)


def codex_file(path, include_exec):
    raw = subprocess.run(["zstd", "-dc", path], capture_output=True).stdout.decode("utf-8", "replace") if path.endswith(".zst") \
        else open(path, errors="replace").read()
    r, sid, cwd = None, None, None
    for line in io.StringIO(raw):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        p = o.get("payload") or {}
        if o.get("type") == "session_meta":
            if p.get("originator") == "codex_exec" and not include_exec:
                return
            sid, cwd = p.get("id"), p.get("cwd")
            continue
        if o.get("type") != "response_item":
            continue
        if p.get("type") in ("function_call", "custom_tool_call", "web_search_call") and r is not None:
            inp = str(p.get("input") or p.get("arguments") or "")
            r["tools"].add("web" if ("web__" in inp or p.get("type") == "web_search_call") else p.get("name") or p.get("type"))
        if p.get("type") != "message":
            continue
        t = text_of(p.get("content")).strip()
        if p.get("role") == "user" and t and not t.startswith(("<", "# AGENTS")):
            emit(r)
            r = {"group": "codex", "model": None, "session": sid, "cwd": cwd, "ts": o.get("timestamp"),
                 "user": t, "final": "", "tools": set(), "docs": []}
        elif p.get("role") == "assistant" and r is not None and t and p.get("phase") in (None, "final_answer"):
            r["final"] = t
    emit(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claude-dir", action="append", help="Claude Code projects dir (repeatable); default ~/.claude/projects")
    ap.add_argument("--codex-dir", default="~/.codex", help="Codex home with sessions/ and archived_sessions/")
    ap.add_argument("--include-exec", action="store_true", help="also index scripted `codex exec` sessions")
    a = ap.parse_args()
    for d in a.claude_dir or ["~/.claude/projects"]:
        for f in glob.glob(os.path.join(os.path.expanduser(d), "*", "*.jsonl")):
            claude_file(f)
    home = os.path.expanduser(a.codex_dir)
    for f in glob.glob(f"{home}/sessions/**/*.jsonl", recursive=True) + glob.glob(f"{home}/archived_sessions/*.zst"):
        codex_file(f, a.include_exec)


if __name__ == "__main__":
    main()
