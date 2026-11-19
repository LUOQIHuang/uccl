"""Add bounded load restoration to the same pinned production transport."""
from pathlib import Path
import hashlib,importlib.util,json,shutil,subprocess
HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
SERIAL=HERE.parent/'serial_pair'
spec=importlib.util.spec_from_file_location('serial_source_overlay',SERIAL/'overlay.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
BASE=base.BASE;MARKER='.urma_pair_load_overlay_v3.json'
replace=base.replace
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def header():
    s=(SERIAL/'native/common.hpp').read_text()
    start=s.index('inline thread_local Pair* current');end=s.index('template<class Submit,class Poll>',start)
    s=s[:start]+(HERE/'native/recorder.inc').read_text()+'\n'+s[end:]
    s=replace(s,'require(!current && (count==1 || count==2)','require(!current && !frame && (count==1 || count==2)')
    i=s.rfind('}');return s[:i]+(HERE/'native/measure.inc').read_text()+'\n}\n'
def changes(name,text):
    s=base.changes(name,text)
    if name=='ep/include/urma_micro.hpp':
        s=replace(s,'bool serial=false);','bool serial=false,size_t serial_lanes=1);')
        s=replace(s,'  void close();','  void load_prepare(size_t limit,int rank,size_t count=4);\n  std::string load_step(size_t seq,size_t limit,size_t count=4);\n  void load_validate(size_t seq);\n  void close();')
    elif name=='ep/src/urma_micro.cpp':
        s=replace(s,'  bool serial=false;','  bool serial=false;size_t serial_lanes=1,load_limit=0;')
        s=replace(s,'size_t max_bytes,bool serial) {','size_t max_bytes,bool serial,size_t serial_lanes) {')
        s=replace(s,'  p->serial=serial;','  uccl::serial_pair::require(serial_lanes==1 || (serial && (serial_lanes==3 || serial_lanes==4)),"unsupported active data lane count");\n  p->serial=serial;p->serial_lanes=serial_lanes;')
        s=replace(s,'configure(true,serial?1:8)','configure(true,serial?serial_lanes:8)')
        s=replace(s,'impl_->serial?1:8','impl_->serial?impl_->serial_lanes:8')
        s=replace(s,'p.serial?1:8','p.serial?p.serial_lanes:8',2)
        s=replace(s,'p.serial && (pairs==1 || pairs==2)','p.serial && p.serial_lanes==1 && (pairs==1 || pairs==2)')
        s=replace(s,'void UrmaMicroSession::close() {',(HERE/'native/session.inc').read_text()+'\nvoid UrmaMicroSession::close() {')
    elif name=='ep/include/verbs_write_micro.hpp':
        s=replace(s,'bool serial=false);','bool serial=false,size_t serial_lanes=1);')
        s=replace(s,'  void close();','  void load_prepare(size_t limit,int rank,size_t count);\n  std::string load_step(size_t seq,size_t limit,size_t count);\n  void load_validate(size_t seq);\n  void close();')
    elif name=='ep/src/verbs_write_micro.cpp':
        s=replace(s,'bool serial=false;','bool serial=false;size_t serial_lanes=1,load_limit=0;')
        s=replace(s,'bool notifications,bool serial) {','bool notifications,bool serial,size_t serial_lanes) {')
        s=replace(s,'p->serial=serial;','p->serial=serial;p->serial_lanes=serial_lanes;\n  ok(serial_lanes==1 || (serial && serial_lanes==3),"invalid verbs load lane count");')
        s=replace(s,'p->serial?1:8','p->serial?p->serial_lanes:8')
        s=replace(s,'p.serial?1:8','p.serial?p.serial_lanes:8',3)
        s=replace(s,'void VerbsWriteSession::close(){',(HERE/'native/verbs_load.inc').read_text()+'\nvoid VerbsWriteSession::close(){')
    elif name=='ep/src/uccl_ep.cc':
        s=replace(s,'  m.def("get_serial_pair_version", []() { return 1; });','  m.def("get_urma_pair_load_version", []() { return 2; });\n  m.def("get_serial_pair_version", []() { return 1; });')
        ctor='.def(nb::init<std::string const&,int,size_t,size_t,bool,bool>())'
        s=replace(s,ctor,ctor+'\n      .def(nb::init<std::string const&,int,size_t,size_t,bool,bool,size_t>())')
        a='.def("serial_prepare",&VerbsWriteSession::serial_prepare)'
        s=replace(s,a,a+'\n      .def("load_prepare",&VerbsWriteSession::load_prepare)\n      .def("load_step",[](VerbsWriteSession& s,size_t seq,size_t limit,size_t count){nb::gil_scoped_release release;return s.load_step(seq,limit,count);})\n      .def("load_validate",&VerbsWriteSession::load_validate)')
        s=replace(s,'.def(nb::init<int,size_t,size_t,bool>())','.def(nb::init<int,size_t,size_t,bool>())\n      .def(nb::init<int,size_t,size_t,bool,size_t>())')
        a='.def("serial_prepare",&UrmaMicroSession::serial_prepare)'
        s=replace(s,a,a+'\n      .def("load_prepare",&UrmaMicroSession::load_prepare,nb::arg("limit"),nb::arg("rank"),nb::arg("count")=4)\n      .def("load_step",[](UrmaMicroSession& s,size_t seq,size_t limit,size_t count){nb::gil_scoped_release release;return s.load_step(seq,limit,count);},nb::arg("seq"),nb::arg("limit"),nb::arg("count")=4)\n      .def("load_validate",&UrmaMicroSession::load_validate)')
    return s
def bundle(root):
    return {p.relative_to(root).as_posix():sha(p) for p in root.rglob('*') if p.is_file() and p.suffix in ('.py','.hpp','.inc','.cpp','.sh') and not any(x in p.parts for x in ('results','__pycache__'))}
def prepare(tree):
    tree=tree.resolve()
    if tree==REPO or REPO in tree.parents:raise ValueError('load source tree must be outside repository')
    deps=bundle(SERIAL);own=bundle(HERE)
    version=subprocess.check_output(['git','-C',str(REPO),'rev-parse',BASE+'^{commit}']).decode().strip();marker=tree/MARKER
    if marker.exists():
        d=json.loads(marker.read_text())
        if d['base']!=version or d['dependencies']!=deps or d['bundle']!=own or any(sha(tree/n)!=v for n,v in d['files'].items()):raise ValueError('LOAD_TREE_CHANGED: use new TREE/BUILD')
        return d
    if tree.exists():raise ValueError('unmarked load tree exists')
    sources={n:changes(n,subprocess.check_output(['git','-C',str(REPO),'show',version+':'+n]).decode()) for n in base.TARGETS}
    common=header()
    subprocess.run(['git','-C',str(REPO),'worktree','add','--quiet','--detach',str(tree),version],check=True)
    for n,s in sources.items():(tree/n).write_text(s)
    (tree/'ep/include/serial_pair.hpp').write_text(common)
    for src,name in [(SERIAL,'serial_pair'),(HERE,'urma_pair_load')]:
        shutil.copytree(src,tree/'test'/name,ignore=shutil.ignore_patterns('results','__pycache__'))
    driver=(SERIAL/'session_driver.py').read_text().replace('SERIAL_PAIR_ALL_PASSED','URMA_PAIR_LOAD_ALL_PASSED')
    (tree/'test/urma_pair_load/session_driver.py').write_text(driver)
    names=base.TARGETS+('ep/include/serial_pair.hpp','test/urma_pair_load/session_driver.py')+tuple('test/serial_pair/'+n for n in deps)+tuple('test/urma_pair_load/'+n for n in own)
    d=dict(version=3,base=version,dependencies=deps,bundle=own,files={n:sha(tree/n) for n in names})
    marker.write_text(json.dumps(d,indent=2));return d
