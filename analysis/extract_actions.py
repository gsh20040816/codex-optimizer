"""Extract shell commands and added code from Claude Code and Codex transcripts as JSONL on stdout.

    python3 analysis/extract_actions.py > actions.jsonl

Records: {"group", "session", "kind": "cmd" | "code", "text", "path"}
  group: claude, cc-gpt (non-Claude models in Claude Code), codex (interactive), codex-exec (scripted runs)
  cmd:   Bash tool commands (Claude Code) / exec_command and shell calls (Codex, including code-mode `exec`)
  code:  Write/Edit new text (Claude Code) / added lines of apply_patch (Codex)
Useful for comparing operational habits (backups, checksums, read-back checks) and defensive code.
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess

seen = set()
JS_STR = r'"((?:[^"\\]|\\.)*)"'


def out(group, session, kind, text, path=""):
    if not text or not text.strip():
        return
    key = (session, kind, text[:200])
    if key in seen:
        return
    seen.add(key)
    print(json.dumps({"group": group, "session": session, "kind": kind, "text": text, "path": path}, ensure_ascii=False))


def patch_added(patch):
    path, lines = "", []
    for ln in patch.splitlines():
        m = re.match(r"\*\*\* (Add|Update) File: (.+)", ln)
        if m:
            if lines:
                yield path, "\n".join(lines)
            path, lines = m.group(2).strip(), []
        elif ln.startswith("+") and not ln.startswith("+++"):
            lines.append(ln[1:])
    if lines:
        yield path, "\n".join(lines)


def js_unescape(s):
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return s


def claude(dirs):
    for d in dirs:
        for f in glob.glob(os.path.join(os.path.expanduser(d), "*", "*.jsonl")):
            for line in open(f, errors="replace"):
                if '"tool_use"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if o.get("type") != "assistant" or o.get("isSidechain"):
                    continue
                m = o.get("message") or {}
                g = "claude" if (m.get("model") or "claude").startswith("claude") else "cc-gpt"
                sid = o.get("sessionId")
                for b in m.get("content") or []:
                    if b.get("type") != "tool_use":
                        continue
                    inp = b.get("input") or {}
                    if b.get("name") == "Bash":
                        out(g, sid, "cmd", inp.get("command", ""))
                    elif b.get("name") == "Write":
                        out(g, sid, "code", inp.get("content", ""), inp.get("file_path", ""))
                    elif b.get("name") == "Edit":
                        out(g, sid, "code", inp.get("new_string", ""), inp.get("file_path", ""))
                    elif b.get("name") == "MultiEdit":
                        for e in inp.get("edits") or []:
                            out(g, sid, "code", e.get("new_string", ""), inp.get("file_path", ""))


def codex(home):
    for f in glob.glob(f"{home}/sessions/**/*.jsonl", recursive=True) + glob.glob(f"{home}/archived_sessions/*.zst"):
        raw = subprocess.run(["zstd", "-dc", f], capture_output=True).stdout.decode("utf-8", "replace") if f.endswith(".zst") \
            else open(f, errors="replace").read()
        g, sid = "codex", None
        for line in io.StringIO(raw):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            p = o.get("payload") or {}
            if o.get("type") == "session_meta":
                sid = p.get("id")
                g = "codex-exec" if p.get("originator") == "codex_exec" else "codex"
                continue
            if o.get("type") != "response_item":
                continue
            t, name = p.get("type"), p.get("name")
            if t == "custom_tool_call" and name == "exec":  # code mode: commands and patches live inside JS
                js = p.get("input") or ""
                for m in re.finditer(r"\bcmd\s*:\s*" + JS_STR, js):
                    out(g, sid, "cmd", js_unescape(m.group(1)))
                for m in re.finditer(r"\bcmd\s*:\s*`((?:[^`\\]|\\.)*)`", js):
                    out(g, sid, "cmd", m.group(1))
                for m in re.finditer(r"\*\*\* Begin Patch.*?\*\*\* End Patch", js.replace("\\n", "\n"), re.S):
                    for path, added in patch_added(m.group(0)):
                        out(g, sid, "code", added, path)
            elif t == "custom_tool_call" and name == "apply_patch":
                for path, added in patch_added(p.get("input") or ""):
                    out(g, sid, "code", added, path)
            elif t == "function_call" and name in ("shell", "exec_command", "container.exec"):
                try:
                    args = json.loads(p.get("arguments") or "{}")
                except ValueError:
                    continue
                cmd = args.get("cmd") or args.get("command")
                if isinstance(cmd, list):
                    cmd = cmd[-1] if cmd[:2] in (["bash", "-lc"], ["zsh", "-lc"]) else " ".join(cmd)
                if isinstance(cmd, str) and "*** Begin Patch" in cmd:
                    for path, added in patch_added(cmd):
                        out(g, sid, "code", added, path)
                else:
                    out(g, sid, "cmd", cmd or "")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claude-dir", action="append", help="Claude Code projects dir (repeatable); default ~/.claude/projects")
    ap.add_argument("--codex-dir", default="~/.codex")
    a = ap.parse_args()
    claude(a.claude_dir or ["~/.claude/projects"])
    codex(os.path.expanduser(a.codex_dir))


if __name__ == "__main__":
    main()
