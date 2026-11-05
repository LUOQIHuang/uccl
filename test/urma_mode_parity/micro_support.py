"""Geometry selection and bounded two-node CPU bootstrap; no GPU imports."""
import collections
import json
import socket
import struct
import time
from pathlib import Path


def select_geometries(path):
    selected=[]
    for label in ('dispatch_fp8_chunk32','dispatch_bf16_chunk32'):
        windows=[json.loads(s) for s in Path(path).read_text().splitlines() if json.loads(s)['label']==label]
        if len(windows)!=1 or windows[0]['dropped'] or windows[0]['modulus']!=4096:
            raise ValueError('missing/truncated WRITE geometry: '+label)
        by_size=collections.Counter()
        for row in windows[0]['writes']:
            if row['bytes']>=4096:by_size[row['bytes']]+=row['count']
        if not by_size:raise ValueError('no payload WRs for '+label)
        size=by_size.most_common(1)[0][0]
        alignments=collections.Counter()
        for row in windows[0]['writes']:
            if row['bytes']==size:alignments[row['src_mod'],row['dst_mod']]+=row['count']
        src,dst=alignments.most_common(1)[0][0]
        if not 0<size<=2*1024*1024 or src%4 or dst%4 or not 0<=src<4096 or not 0<=dst<4096:
            raise ValueError('invalid WRITE geometry')
        selected.append(dict(label=label,bytes=size,src_mod=src,dst_mod=dst,
            selected_count=alignments[src,dst],payload_writes=sum(by_size.values()),
            size_histogram=dict(by_size)))
    return selected


def points(geometries):
    result=[]
    for geometry in geometries:
        for window in (1,4):
            for fadd in (False,True):
                result.append(dict(geometry=geometry,window=window,messages=32,fadd=fadd,visible=False,
                    label=geometry['label']+f'_batch_w{window}_'+('write_fadd' if fadd else 'write_only')))
        for visible in (False,True):
            result.append(dict(geometry=geometry,window=1,messages=8,fadd=True,visible=visible,
                label=geometry['label']+'_step_'+('gpu_observed' if visible else 'cqe_completed')))
    return result


class Peer:
    def __init__(self,rank,address,port,timeout=180):
        self.rank=rank
        if rank==0:
            server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            server.settimeout(timeout);server.bind(('',port));server.listen(1)
            try:self.socket,_=server.accept()
            finally:server.close()
        else:
            deadline=time.monotonic()+timeout
            while True:
                try:self.socket=socket.create_connection((address,port),timeout=3);break
                except OSError:
                    if time.monotonic()>deadline:raise
                    time.sleep(.2)
        self.socket.settimeout(timeout)
        self.socket.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)

    def _send(self,value):
        raw=json.dumps(value,separators=(',',':')).encode()
        if len(raw)>1024*1024:raise ValueError('micro control frame too large')
        self.socket.sendall(struct.pack('!I',len(raw))+raw)

    def _receive(self):
        def exact(n):
            out=bytearray()
            while len(out)<n:
                chunk=self.socket.recv(n-len(out))
                if not chunk:raise RuntimeError('micro peer disconnected')
                out.extend(chunk)
            return bytes(out)
        size=struct.unpack('!I',exact(4))[0]
        if not 0<size<=1024*1024:raise ValueError('invalid micro control frame size')
        return json.loads(exact(size))

    def exchange(self,tag,value):
        local=dict(tag=tag,value=value)
        if self.rank==0:self._send(local);other=self._receive()
        else:other=self._receive();self._send(local)
        if other['tag']!=tag:raise RuntimeError('micro control stage mismatch')
        return other['value']

    def agree(self,tag,value):
        if self.exchange(tag,value)!=value:raise RuntimeError('micro peer configuration/status mismatch: '+tag)

    def close(self):self.socket.close()
