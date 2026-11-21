"""Real server + recording ACP peer, synthetic input, no model calls.

Run: python3 scripts/domain-profile-probe.py [path/to/orx]
Uses separate data/config/cache roots; never replaces HOME or CODEX_HOME.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


checkout = Path(__file__).resolve().parent.parent
binary = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else checkout / "target/debug/orx"
root = Path(tempfile.mkdtemp(prefix="orx-domain-probe-"))
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
results = {}


class Server:
    def __init__(self, name, profile=None):
        self.root = root / name
        self.root.mkdir()
        self.record = self.root / "record.jsonl"
        self.env = {k: v for k, v in os.environ.items() if k in ("HOME", "TMPDIR", "LANG", "USER", "LOGNAME")}
        for folder in ("bin", "data", "cache", "config", "claude", "cursor", "xdg-data"):
            (self.root / folder).mkdir()
        for name in ("claude", "codex", "opencode", "agent", "cursor-agent", "agy", "gh"):
            stub = self.root / "bin" / name
            stub.write_text('#!/bin/sh\n[ "$1" = "--version" ] && echo "0.0.0 probe-stub" && exit 0\n[ "$1" = "auth" ] && echo \'{"loggedIn":false}\'\nexit 1\n')
            stub.chmod(0o755)
        self.env.update({
            "PATH": str(self.root / "bin") + ":/usr/bin:/bin:/usr/sbin:/sbin", "SHELL": "/bin/sh",
            "ORX_DATA_DIR": str(self.root / "data"), "ORX_CACHE_DIR": str(self.root / "cache"),
            "XDG_CONFIG_HOME": str(self.root / "config"), "XDG_DATA_HOME": str(self.root / "xdg-data"),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"), "CLAUDE_SECURESTORAGE_CONFIG_DIR": str(self.root / "claude"),
            "CURSOR_CONFIG_DIR": str(self.root / "cursor"), "CURSOR_DATA_DIR": str(self.root / "cursor"),
            "ORX_NO_UPDATE_CHECK": "1", "OPENRESEARCH_API_URL": "http://127.0.0.1:9",
            "ALPHAXIV_API_URL": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "127.0.0.1,localhost",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "Domain probe", "GIT_AUTHOR_EMAIL": "probe@example.invalid",
            "GIT_COMMITTER_NAME": "Domain probe", "GIT_COMMITTER_EMAIL": "probe@example.invalid",
        })
        if profile:
            self.env["ORX_AGENT_PROFILE"] = str(profile)
        self.command = [str(binary), "up", "--port", "0", "--no-browser", "--no-agent", "--no-telemetry"]
        self.process = None

    def start(self):
        self.log = (self.root / "server.log").open("w")
        self.process = subprocess.Popen(self.command, env=self.env, stdout=self.log, stderr=self.log, start_new_session=True)
        match = self.until(lambda: re.search(r'dashboard on (http://127\.0\.0\.1:\d+)', (self.root / "server.log").read_text()), "server start")
        self.url = match[1]

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait()
            self.log.close()
            self.process = None

    def until(self, predicate, label):
        end = time.monotonic() + 40
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            if self.process.poll() is not None:
                raise RuntimeError((self.root / "server.log").read_text()[-2000:])
            time.sleep(0.1)
        raise TimeoutError(label)

    def api(self, route, body=None):
        request = urllib.request.Request(self.url + route, data=json.dumps(body).encode() if body is not None else None,
                                         headers={"Content-Type": "application/json", "Origin": self.url})
        with opener.open(request, timeout=25) as response:
            return json.load(response)

    def create_chat(self, name="Domain probe"):
        agent = checkout / "tests/fixtures/domain-profile-acp.py"
        harness = self.api("/api/harnesses/acp", {"name": "Domain recorder", "executable": sys.executable,
                                                "arguments": [str(agent), str(self.record)]})["id"]
        self.until(lambda: next((h for h in self.api("/api/harnesses")["harnesses"] if h["id"] == harness and h.get("defaultModel")), None), "agent discovery")
        self.api("/api/onboarding/complete", {"harness": harness, "model": "recorder"})
        repo = self.root / "project"
        repo.mkdir()
        (repo / "source.txt").write_text("Synthetic source: keep source references with every draft.\n")
        for args in (("init", "-q"), ("add", "source.txt"), ("commit", "-qm", "Synthetic input")):
            subprocess.run(["git", *args], cwd=repo, env=self.env, check=True, capture_output=True)
        project = self.api("/api/projects", {"name": name, "path": str(repo), "githubSyncEnabled": False})["project"]["id"]
        self.session = self.api("/api/chat/sessions", {"projectId": project, "harness": harness, "model": "recorder"})["session"]["id"]

    def turn(self, number):
        self.api(f"/api/chat/sessions/{self.session}/message", {"text": f"Make a grounded draft from source.txt. Probe turn {number}."})
        def records():
            return [json.loads(line) for line in self.record.read_text().splitlines()] if self.record.exists() else []
        entries = self.until(lambda: (rows if len(rows) >= number else None) if (rows := records()) else None, "recorded prompt")
        self.until(lambda: not self.api(f"/api/chat/sessions/{self.session}")["session"].get("busy", False), "settled turn")
        return entries[-1]

    def rejects(self, env, expected):
        result = subprocess.run(self.command, env=env, capture_output=True, text=True, timeout=20)
        assert result.returncode != 0, "unexpectedly started"
        assert expected in result.stderr, result.stderr


def main():
    profile_dir = root / "profile"
    shutil.copytree(checkout / "examples/domain-profiles/content", profile_dir)
    profile_path = profile_dir / "profile.toml"
    servers = []
    try:
        research = Server("research")
        servers.append(research)
        research.start()
        research.create_chat()
        default = research.turn(1)
        default_text = default["files"][".openresearch/agent/autoresearch-local.md"]
        assert "# OpenResearch agent" in default_text
        assert "orx-experiment-tree" in default["skills"]
        assert "orx-experiment-runs" in default["skills"]
        research.stop()
        research.rejects(dict(research.env, ORX_AGENT_PROFILE=str(profile_path)), "fresh ORX_DATA_DIR")
        results["research"] = {"skills": default["skills"], "playbook": default_text, "existing_data_retarget_rejected": True}

        content = Server("content", profile_path)
        servers.append(content)
        content.start()
        content.create_chat()
        first = content.turn(1)
        text = first["files"][".openresearch/agent/autoresearch-local.md"]
        assert "# Agent profile: content-from-sessions" in text
        assert "Source, Claim, Draft and Review" in text
        assert "# OpenResearch agent" not in text
        assert "orx-experiment-runs" not in text
        assert first["skills"] == ["orx-agent-delegation"], first["skills"]
        worktree = Path(first["cwd"])
        resource = worktree / ".openresearch/domain/domain.yaml"
        original = resource.read_text()
        (profile_dir / "domain.yaml").write_text("changed source after startup")
        resource.write_text("local drift")
        (worktree / ".openresearch/agent/autoresearch-local.md").write_text("local drift")
        second = content.turn(2)
        assert second["files"][".openresearch/agent/autoresearch-local.md"] == text
        assert resource.read_text() == original
        assert (worktree / ".openresearch/domain/check-state.py").is_file()
        content.stop()
        content.rejects(content.env, "different agent profile")
        default_env = dict(content.env)
        del default_env["ORX_AGENT_PROFILE"]
        content.rejects(default_env, "different agent profile")
        (profile_dir / "domain.yaml").write_text(original)
        content.start()
        third = content.turn(3)
        assert third["files"][".openresearch/agent/autoresearch-local.md"] == text
        results["content"] = {"skills": first["skills"], "playbook": text, "mcp_servers": first["servers"],
                              "snapshot_immutable": True, "drift_refreshed": True, "restart_resumed": True,
                              "changed_profile_rejected": True, "default_profile_rejected": True}
        content.stop()
        (root / "result.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps({"ok": True, "evidence": str(root / "result.json"), "research_skills": len(default["skills"]),
                          "content_skills": first["skills"], "model_inference": False}, indent=2))
    finally:
        for server in servers:
            server.stop()
        print("Probe files: " + str(root))


if __name__ == "__main__":
    main()
