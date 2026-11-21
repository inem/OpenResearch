"""macOS native window/storage probe; type check, restart-content, or stop.

Uses synthetic chats and a recording ACP peer; no model inference.
"""
import importlib.util
import json
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
checkout = Path(__file__).resolve().parent.parent
if sys.platform != 'darwin':
    raise SystemExit('This native-window probe requires macOS 14 or later.')
spec = importlib.util.spec_from_file_location('probe', checkout/'scripts/domain-profile-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
root = probe.root
servers, windows, reports = [], [], []
phase = {}
changed = threading.Event()

def bundle(name, ident):
    path = root/(name+'.app')
    (path/'Contents/MacOS').mkdir(parents=True)
    with (path/'Contents/Info.plist').open('wb') as f:
        plistlib.dump({'CFBundleName': name, 'CFBundleDisplayName': name, 'CFBundleIdentifier': ident,
                      'CFBundleExecutable': 'OpenResearch', 'CFBundlePackageType': 'APPL',
                      'NSHighResolutionCapable': True}, f)
    binary = path/'Contents/MacOS/OpenResearch'
    shutil.copy2(probe.binary, binary)
    return binary

def window(binary, port, env, identifier, label):
    output=(root/(label.replace(' ','-')+'.log')).open('a')
    process=subprocess.Popen([str(binary),'--dev-desktop',str(port)],
        env=dict(env, ORX_DESKTOP_INSTANCE_ID=identifier, ORX_DESKTOP_INSTANCE_LABEL=label),
        stdout=output,stderr=output,start_new_session=True)
    item=(process,output)
    windows.append(item)
    return item

def close(item):
    process,output=item
    if process.poll() is None:
        os.killpg(process.pid,signal.SIGTERM)
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL)
            process.wait()
    output.close()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(('''<!doctype html><html><title>Native storage isolation probe</title>
<body style="font:24px system-ui;padding:60px"><h1>Native storage isolation probe</h1><pre id="result"></pre>
<script>
const label=LABEL;
const key='orx-isolation-proof';
const beforeLocal=localStorage.getItem(key);
const beforeCookie=document.cookie.split('; ').find(v=>v.startsWith(key+'='))?.split('=')[1] ?? null;
const request=indexedDB.open('orx-isolation-proof',1);
request.onupgradeneeded=()=>request.result.createObjectStore('proof');
request.onsuccess=()=>{
 const db=request.result, tx=db.transaction('proof','readwrite'), store=tx.objectStore('proof');
 let beforeDb=null;
 const read=store.get('label');
 read.onsuccess=()=>{beforeDb=read.result ?? null; store.put(label,'label');};
 tx.oncomplete=async()=>{
  db.close(); localStorage.setItem(key,label);
  document.cookie=key+'='+label+'; Path=/; Max-Age=86400; SameSite=Strict';
  const report={label,beforeLocal,beforeCookie,beforeDb};
  document.getElementById('result').textContent=JSON.stringify(report,null,2);
  await fetch('/report',{method:'POST',body:JSON.stringify(report)});
 };
};
</script></body></html>'''.replace('LABEL',json.dumps(phase['label']))).encode())
    def do_POST(self):
        reports.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
        changed.set()

fixture=ThreadingHTTPServer(('127.0.0.1',0),Handler)
threading.Thread(target=fixture.serve_forever,daemon=True).start()
try:
    content_profile=checkout/'examples/domain-profiles/content/profile.toml'
    atlas_dir=root/'atlas-profile'
    atlas_dir.mkdir()
    (atlas_dir/'profile.toml').write_text('version=1\nname="atlas-toy"\nplaybook="playbook.md"\nbundled_skills=[]\n')
    (atlas_dir/'playbook.md').write_text('Work with Term, Distinction, Source and OpenQuestion. This is a synthetic test.')
    for name,profile in [('Content',content_profile),('Atlas',atlas_dir/'profile.toml')]:
        server=probe.Server(name,profile)
        servers.append(server)
        server.start()
        server.create_chat(name+' isolation test')
        server.api('/api/settings/ui-state',{'tourCompleted':True,'nestedDelegation':name=='Content','opencodeFreeModels':False})
        server.turn(1)

    native=bundle('ORX Browser Isolation Test','dev.inem.orx.browser-isolation-test')
    ids={name:str(uuid.uuid4()) for name in ('A','B')}
    for label,expected in [('A',None),('B',None),('A','A'),('B','B')]:
        phase['label']=label
        changed.clear()
        item=window(native,fixture.server_port,servers[0].env,ids[label],'Storage '+label)
        if not changed.wait(30): raise RuntimeError('No native storage report')
        report=reports[-1]
        assert all(report[key]==expected for key in ('beforeLocal','beforeCookie','beforeDb')),report
        close(item)
    fixture.shutdown()
    print('NATIVE_STORAGE_PASSED '+json.dumps(reports),flush=True)

    bundles=[bundle('ORX Content Test','dev.inem.orx.content-test'),bundle('ORX Atlas Test','dev.inem.orx.atlas-test')]
    app_ids=[str(uuid.uuid4()),str(uuid.uuid4())]
    current=[]
    for index,server in enumerate(servers):
        current.append(window(bundles[index],int(server.url.rsplit(':',1)[1]),server.env,app_ids[index],server.root.name))
    manifest={'root':str(root),'apps':[{'name':s.root.name,'url':s.url,'session':s.session,'uuid':app_ids[i],
                  'binary':str(bundles[i])} for i,s in enumerate(servers)],'nativeStorageReports':reports}
    (root/'desktop-isolation-result.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print('WINDOWS_READY '+json.dumps(manifest),flush=True)
    while True:
        command=input().strip()
        if command=='stop': break
        if command=='restart-content':
            close(current[0])
            server=servers[0]
            current[0]=window(bundles[0],int(server.url.rsplit(':',1)[1]),server.env,app_ids[0],'Content')
            print('CONTENT_WINDOW_REOPENED',flush=True)
        if command=='check':
            states=[s.api('/api/settings/ui-state') for s in servers]
            assert states[0]['nestedDelegation'] is True,states[0]
            assert states[1]['nestedDelegation'] is False,states[1]
            lists=[s.api('/api/chat/sessions?scope=all')['sessions'] for s in servers]
            assert all(any(row['id']==s.session for row in lists[i]) for i,s in enumerate(servers)),lists
            assert all(all(row['id']!=servers[1-i].session for row in lists[i]) for i in range(2)),lists
            print('SETTINGS_AND_CHATS_ISOLATED',flush=True)
finally:
    for item in windows: close(item)
    for server in servers: server.stop()
    print('All test windows and servers stopped.',flush=True)
