#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <inttypes.h>
#include <link.h>
#include <pthread.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "ummu_mapt.h"

/* These offsets are also checked against the installed library's disassembly. */
_Static_assert(offsetof(struct ummu_mapt_info, mode) == 20, "unexpected libummu ABI");
_Static_assert(offsetof(struct ummu_mapt_info, tid) == 8, "unexpected libummu ABI");

struct symbol_lookup { const char *symbol; void *result; };

static int lookup_library(struct dl_phdr_info *info, size_t size, void *arg)
{
    (void)size;
    struct symbol_lookup *lookup = arg;
    const char *name = strrchr(info->dlpi_name, '/');
    name = name ? name + 1 : info->dlpi_name;
    if (strncmp(name, "libummu.so", 10) != 0) return 0;
    void *handle = dlopen(info->dlpi_name, RTLD_NOW | RTLD_NOLOAD);
    if (!handle) return 0;
    lookup->result = dlsym(handle, lookup->symbol);
    dlclose(handle);
    return lookup->result != NULL;
}

static void *lookup(const char *symbol)
{
    struct symbol_lookup result = {symbol, NULL};
    dl_iterate_phdr(lookup_library, &result);
    return result.result;
}

/* Check that every address belongs to a readable mapping in this process. */
static int readable(const void *address, size_t length)
{
    uintptr_t begin = (uintptr_t)address, end = begin + length;
    if (!begin || end < begin) return 0;
    FILE *maps = fopen("/proc/self/maps", "r");
    if (!maps) return 0;
    char line[512], perm[5];
    unsigned long low, high;
    int found = 0;
    while (fgets(line, sizeof(line), maps)) {
        if (sscanf(line, "%lx-%lx %4s", &low, &high, perm) == 3 &&
            perm[0] == 'r' && begin >= low && end <= high) {
            found = 1;
            break;
        }
    }
    fclose(maps);
    return found;
}

static void trace_mapt(uint32_t tid, void *data, size_t length)
{
    typedef struct ummu_ctx_info *(*get_ctx_t)(void);
    typedef void *(*map_get_t)(void *, uint32_t);
    get_ctx_t get_ctx = (get_ctx_t)lookup("get_ummu_ctx");
    map_get_t map_get = (map_get_t)lookup("ummu_map_get");
    if (!get_ctx || !map_get) { fprintf(stderr, "MAPT_OBSERVE_NO_SYMBOLS\n"); return; }
    struct ummu_ctx_info *ctx = get_ctx();
    if (!readable(ctx, sizeof(*ctx)) || pthread_mutex_trylock(&ctx->ctx_mutex)) {
        fprintf(stderr, "MAPT_OBSERVE_CTX_UNAVAILABLE\n"); return;
    }
    struct ummu_mapt_info *info = map_get(ctx->mapt_map, tid);
    if (!readable(info, sizeof(*info)) || info->tid != tid || !info->valid ||
        (info->mode != MAPT_MODE_ENTRY && info->mode != MAPT_MODE_TABLE) ||
        pthread_mutex_trylock(&info->mapt_mutex)) {
        fprintf(stderr, "MAPT_OBSERVE_INFO_INVALID tid=%u\n", tid);
        pthread_mutex_unlock(&ctx->ctx_mutex);
        return;
    }
    uint64_t address = (uint64_t)(uintptr_t)data;
    fprintf(stderr, "MAPT_OBSERVE tid=%u va=%p len=%zu mode=%u\n",
            tid, data, length, (unsigned)info->mode);
    if (info->mode == MAPT_MODE_ENTRY) {
        struct ummu_mapt_entry_node *n = info->block_base.entry_block;
        if (readable(n, sizeof(*n))) {
            uint64_t base = ADDR_FULL(n->base_low, n->base_high);
            uint64_t limit = ADDR_FULL(n->limit_low, n->limit_high);
            fprintf(stderr, "MAPT_ENTRY tid=%u valid=%u perm=%u e_bit=%u base=%" PRIx64 " limit=%" PRIx64 " covers=%d\n",
                    tid, n->valid, n->permission, n->e_bit, base, limit,
                    address >= base && address <= limit && length - 1 <= limit - address);
        } else fprintf(stderr, "MAPT_OBSERVE_ENTRY_UNREADABLE tid=%u\n", tid);
    } else {
        struct ummu_mapt_table_ctx *table = info->block_base.table_ctx;
        if (!readable(table, sizeof(*table))) {
            fprintf(stderr, "MAPT_OBSERVE_TABLE_UNREADABLE tid=%u\n", tid);
            goto out;
        }
        struct ummu_mapt_table_node parent = {0};
        for (unsigned level = 0; level <= MAX_LEVEL_INDEX; ++level) {
            uint32_t block_index = parent.next_lv_index;
            uint32_t offset = TABLE_LVL_OFFSET(parent.next_lv_offset_low, parent.next_lv_offset_high);
            uint32_t index = (uint32_t)GET_LEVEL_BLOCK_INDEX(address, level);
            if (block_index >= INDEX_MAX_SIZE) { fprintf(stderr, "MAPT_BAD_BLOCK tid=%u\n", tid); break; }
            struct ummu_mapt_block *block = table->mapt_block_array[block_index];
            if (!readable(block, sizeof(*block)) || !block->block_addr ||
                (uint64_t)(offset + index + 1) * sizeof(parent) > block->blk_size) {
                fprintf(stderr, "MAPT_BAD_BLOCK tid=%u level=%u block=%u\n", tid, level, block_index); break;
            }
            struct ummu_mapt_table_node *n = (struct ummu_mapt_table_node *)block->block_addr + offset + index;
            if (!readable(n, sizeof(*n))) { fprintf(stderr, "MAPT_NODE_UNREADABLE tid=%u\n", tid); break; }
            uint64_t base = ADDR_FULL(n->base_low, n->base_high);
            uint64_t limit = ADDR_FULL(n->limit_low, n->limit_high);
            uint64_t relative = address & GET_LEVEL_RANGE_MASK(level);
            int covers = relative >= base && relative <= limit && length - 1 <= limit - relative;
            fprintf(stderr, "MAPT_NODE tid=%u level=%u valid=%u type=%u perm=%u e_bit=%u f_bit=%u base=%" PRIx64 " limit=%" PRIx64 " covers=%d\n",
                    tid, level, n->valid, n->type, n->permission, n->e_bit, n->f_bit, base, limit, covers);
            if (!n->valid || n->type == 1) break;
            parent = *n;
        }
    }
out:
    pthread_mutex_unlock(&info->mapt_mutex);
    pthread_mutex_unlock(&ctx->ctx_mutex);
}

int ummu_grant(uint32_t tid, void *data, size_t size,
               enum ummu_mapt_perm perm, struct ummu_seg_attr *attr)
{
    typedef int (*grant_t)(uint32_t, void *, size_t, enum ummu_mapt_perm, struct ummu_seg_attr *);
    grant_t real = (grant_t)lookup("ummu_grant");
    if (!real) { fprintf(stderr, "MAPT_OBSERVE_GRANT_UNAVAILABLE\n"); abort(); }
    int rc = real(tid, data, size, perm, attr);
    int saved_errno = errno;
    if (rc == 0 && size) trace_mapt(tid, data, size);
    errno = saved_errno;
    return rc;
}
