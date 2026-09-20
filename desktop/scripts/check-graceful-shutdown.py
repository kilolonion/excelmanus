"""Local integration check: an active SSE request cannot block desktop shutdown forever."""
import os,sys,tempfile,subprocess,socket,time,urllib.request
from pathlib import Path
root=Path.cwd()
with tempfile.TemporaryDirectory(prefix='excelmanus-drain-') as temporary:
    home=Path(temporary); (home/'data').mkdir()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    source=f'''import sys,asyncio
sys.path.insert(0,{str(root)!r})
from excelmanus.settings_runtime import override_settings
override_settings({{"EXCELMANUS_EXA_SEARCH_ENABLED":"false"}})
from excelmanus import api
from starlette.responses import StreamingResponse
@api.app.get("/desktop-drain-probe")
async def held_request():
    async def stream():
        yield b"data: ready\\n\\n"
        await asyncio.sleep(300)
    return StreamingResponse(stream(),media_type="text/event-stream")
api.main()
'''
    env={k:v for k,v in os.environ.items() if not k.startswith('EXCELMANUS_')}
    env.update(EXCELMANUS_HOME=str(home),EXCELMANUS_DESKTOP='1',EXCELMANUS_DESKTOP_CONTROL_STDIN='1')
    with (home/'log').open('w') as log:
        child=subprocess.Popen([sys.executable,'-X','utf8','-c',source,'--host','127.0.0.1','--port',str(port)],cwd=home/'data',env=env,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,text=True)
        try:
            deadline=time.monotonic()+25
            while True:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/health',timeout=1):break
                except OSError:
                    if time.monotonic()>deadline:raise
                    time.sleep(.2)
            response=urllib.request.urlopen(f'http://127.0.0.1:{port}/desktop-drain-probe',timeout=20)
            assert response.readline()==b'data: ready\n'
            started=time.monotonic()
            child.stdin.write('shutdown\n');child.stdin.flush()
            child.wait(timeout=25)
            elapsed=time.monotonic()-started
            response.close()
            assert child.returncode==0,child.returncode
            assert 'API 服务已关闭' in (home/'log').read_text(encoding='utf-8')
            print(f'SSE_REQUEST_DRAIN_OK elapsed={elapsed:.1f}s exit={child.returncode}')
        finally:
            if child.poll() is None:child.kill();child.wait()
