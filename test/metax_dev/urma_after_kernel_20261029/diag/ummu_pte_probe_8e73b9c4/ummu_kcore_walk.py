"""Read only one live test process's TID/domain from /proc/kcore via GDB.

The target process keeps its URMA segment alive while waiting for a client.
No kernel functions are called and no memory is modified.
"""
import os
import gdb

def typ(name):
    return gdb.lookup_type('struct ' + name)

def ptr(addr, name):
    if not addr:
        raise RuntimeError('null ' + name)
    return gdb.Value(addr).cast(typ(name).pointer()).dereference()

def offset(name, field):
    for f in typ(name).fields():
        if f.name == field and f.bitpos is not None and f.bitpos % 8 == 0:
            return f.bitpos // 8
    raise RuntimeError('missing field ' + name + '.' + field)

def number(v):
    return int(v.cast(gdb.lookup_type('unsigned long')))

tid = int(os.environ['UCCL_DIAG_TID'], 10)
va = int(os.environ['UCCL_DIAG_VA'], 16)
core_global = int(os.environ['UCCL_DIAG_CORE_GLOBAL'], 16)
expected_map = int(os.environ['UCCL_DIAG_EXPECT_MAP'], 16)
longp = gdb.lookup_type('unsigned long').pointer()
core_addr = int(gdb.Value(core_global).cast(longp).dereference())
core = ptr(core_addr, 'ummu_core_device')
mgr = core['tid_manager'].dereference()
entry = number(mgr['token_ids']['xa_head'])
depth = 0
while (entry & 3) == 2 and entry > 4096:
    node = ptr(entry - 2, 'xa_node')
    shift = int(node['shift'])
    if shift < 0 or shift > 60 or (depth == 0 and (tid >> (shift + 6)) != 0) or depth > 10:
        raise RuntimeError('invalid xarray index or depth')
    entry = number(node['slots'][(tid >> shift) & 63])
    depth += 1
if not entry or (entry & 3):
    raise RuntimeError('TID not found or is an xarray internal entry')
tid_data = ptr(entry, 'ummu_tid_data')
device = tid_data['device'].dereference()
group = device['iommu_group'].dereference()
domain_addr = number(group['domain'])
logic_addr = domain_addr - offset('logic_ummu_domain', 'base_domain') - offset('ummu_base_domain', 'domain')
logic = ptr(logic_addr, 'logic_ummu_domain')
agent_addr = number(logic['agent_domain'])
agent = ptr(agent_addr, 'ummu_base_domain')
if int(agent['tid']) != tid:
    raise RuntimeError('agent TID does not match observed target')
map_fn = number(agent['domain']['ops'].dereference()['map_pages'])
if map_fn != expected_map:
    raise RuntimeError('agent map_pages is not the loaded UMMU implementation')
uaddr = agent_addr - offset('ummu_domain', 'base_domain')
udomain = ptr(uaddr, 'ummu_domain')
ops = number(udomain['cfgs']['pgtbl_ops'])
data_addr = ops - offset('arm_lpae_io_pgtable', 'iop') - offset('io_pgtable', 'ops')
data = ptr(data_addr, 'arm_lpae_io_pgtable')
fmt = int(data['iop']['fmt'])
cfg = data['iop']['cfg']
print('WALK_TID=%d VA=0x%x DATA_TID=%d XARRAY_DEPTH=%d' % (tid, va, int(agent['tid']), depth))
print('WALK_PTRS device=0x%x domain=0x%x agent=0x%x ops=0x%x data=0x%x' %
      (number(tid_data['device']), domain_addr, agent_addr, ops, data_addr))
print('WALK_PGTABLE stage=%d fmt=%d pgd_bits=%d start_level=%d bits_per_level=%d pgd=0x%x ias=%d oas=%d bitmap=0x%x' %
      (int(udomain['cfgs']['stage']), fmt, int(data['pgd_bits']), int(data['start_level']),
       int(data['bits_per_level']), number(data['pgd']), int(cfg['ias']), int(cfg['oas']), int(cfg['pgsize_bitmap'])))
print('WALK_CFG_S1_TTBR=0x%x S1_MAIR=0x%x S2_VTTBR=0x%x' %
      (int(cfg['arm_lpae_s1_cfg']['ttbr']), int(cfg['arm_lpae_s1_cfg']['mair']),
       int(cfg['arm_lpae_s2_cfg']['vttbr'])))
print('WALK_DOMAIN_OK')
