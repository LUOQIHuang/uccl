#include <pthread.h>
#include <stdint.h>
#include <string.h>
#include "ummu_mapt.h"

static struct ummu_ctx_info ctx = {.ctx_mutex = PTHREAD_MUTEX_INITIALIZER};
static struct ummu_mapt_info info = {.mapt_mutex = PTHREAD_MUTEX_INITIALIZER};
static struct ummu_mapt_table_ctx table;
static struct ummu_mapt_block block;
static struct ummu_mapt_table_node nodes[512];
static struct ummu_mapt_entry_node entry;

struct ummu_ctx_info *get_ummu_ctx(void) { return &ctx; }
void *ummu_map_get(void *map, uint32_t tid) { (void)map; return info.tid == tid ? &info : 0; }

int ummu_grant(uint32_t tid, void *data, size_t len, enum ummu_mapt_perm perm, struct ummu_seg_attr *attr)
{
    (void)attr;
    uint64_t addr = (uint64_t)(uintptr_t)data;
    info.tid = tid;
    info.valid = 1;
    if (tid == 42) {
        memset(nodes, 0, sizeof(nodes));
        block.block_addr = nodes;
        block.blk_size = sizeof(nodes);
        table.mapt_block_array[0] = &block;
        info.block_base.table_ctx = &table;
        info.mode = MAPT_MODE_TABLE;
        struct ummu_mapt_table_node *n = &nodes[GET_LEVEL_BLOCK_INDEX(addr, 0)];
        uint64_t base = addr & GET_LEVEL_RANGE_MASK(0);
        uint64_t limit = base + len - 1;
        n->valid = 1; n->type = 1; n->permission = perm;
        n->base_low = (uint32_t)base; n->base_high = (base >> 32) & 0x7f;
        n->limit_low = (uint32_t)limit; n->limit_high = (limit >> 32) & 0x7f;
    } else {
        memset(&entry, 0, sizeof(entry));
        entry.valid = 1; entry.permission = perm;
        entry.base_low = (uint32_t)addr; entry.base_high = (addr >> 32) & 0xffff;
        entry.limit_low = (uint32_t)(addr + len - 1); entry.limit_high = ((addr + len - 1) >> 32) & 0xffff;
        info.block_base.entry_block = &entry;
        info.mode = MAPT_MODE_ENTRY;
    }
    return 0;
}
