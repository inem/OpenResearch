"""Recording ACP peer. It records actual ORX inputs; it is not an LLM."""
import json
import os
import sys
import uuid
from pathlib import Path

record = Path(sys.argv[1])
sessions = {}


def emit(**message):
    print(json.dumps(dict(jsonrpc="2.0", **message)), flush=True)


for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get("method"), message.get("params", {})
    ident = message.get("id")
    if method == "initialize":
        result = {"protocolVersion": 1, "agentInfo": {"name": "Template recorder", "version": "1"},
                  "agentCapabilities": {"loadSession": True}}
    elif method == "session/new":
        sid = str(uuid.uuid4())
        sessions[sid] = {"cwd": params["cwd"], "servers": [s["name"] for s in params.get("mcpServers", [])]}
        result = {"sessionId": sid, "models": {"currentModelId": "recorder", "availableModels": [{"modelId": "recorder", "name": "Recorder (no LLM)"}]}}
    elif method == "session/load":
        sessions[params["sessionId"]] = {"cwd": params["cwd"], "servers": [s["name"] for s in params.get("mcpServers", [])]}
        result = {}
    elif method == "session/set_model":
        result = {}
    elif method == "session/prompt":
        sid = params["sessionId"]
        context = sessions[sid]
        cwd = Path(context["cwd"])
        prompt = "\n".join(block.get("text", "") for block in params.get("prompt", []))
        playbook = cwd / ".openresearch/agent/PLAYBOOK.md"
        candidates = list((cwd / ".openresearch").rglob("*.md")) if (cwd / ".openresearch").exists() else []
        entry = dict(context, prompt=prompt, files={str(p.relative_to(cwd)):p.read_text() for p in candidates},
                     skills=sorted(p.parent.name for p in (cwd/".agents/skills").glob("*/SKILL.md")),
                     uploaded_files=sorted(str(p.relative_to(cwd)) for p in (cwd/".agents/skills/content-from-sessions").rglob("*") if p.is_file()))
        if context["servers"]:
            with record.open("a") as out:
                out.write(json.dumps(entry)+"\n")
            emit(method="session/update", params={"sessionId":sid,"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Recorder captured the delivered prompt and files. No model inference occurred."}}})
        result = {"stopReason":"end_turn"}
    elif method == "session/cancel":
        continue
    else:
        if ident is not None:
            emit(id=ident,error={"code":-32601,"message":"unsupported"})
        continue
    emit(id=ident,result=result)
