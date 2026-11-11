"""Keep reference GPU events enabled during the benchmark's correctness tests."""
import atexit
import json
from event_runtime import Capture


def install(ep,buffer,case,capture_factory=None):
    original=buffer.Buffer.dispatch
    state=dict(capture=None,closed=False)
    def dispatch(self,*args,**kwargs):
        if state['capture'] is None:
            cap=(capture_factory or Capture)(ep.__file__);cap.begin();state['capture']=cap
        return original(self,*args,**kwargs)
    buffer.Buffer.dispatch=dispatch
    def finish(verified=False):
        if state['closed']:return
        state['closed']=True;buffer.Buffer.dispatch=original
        cap=state['capture']
        if cap is None:
            if verified:raise RuntimeError('reference correctness capture never started')
            return
        data=cap.end();data.pop('records',None);data['verified']=verified
        (case/'reference_correctness.json').write_text(json.dumps(data,indent=2))
        if verified:print('REFERENCE_CORRECTNESS_EVENTS_PASSED',flush=True)
    atexit.register(finish)
    return finish
