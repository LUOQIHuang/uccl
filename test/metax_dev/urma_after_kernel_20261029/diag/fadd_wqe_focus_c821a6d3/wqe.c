/* Read only this probe's own queue; never change provider state or WQEs. */
#include "udma_u_jetty.h"
#include "udma_u_jfs.h"
#include <inttypes.h>
#include <string.h>

_Static_assert(offsetof(struct udma_u_jetty, base) == 0, "jetty base layout");
_Static_assert(sizeof(struct udma_jfs_sqe_ctl) == 48, "control layout");
_Static_assert(sizeof(struct udma_wqe_sge) == 16, "sge layout");

void *diag_wqe_before(urma_jetty_t *jetty, uint32_t *producer)
{
    struct udma_u_jetty_queue *sq = &((struct udma_u_jetty *)jetty)->sq;
    uintptr_t begin = (uintptr_t)sq->qbuf;
    uintptr_t end = (uintptr_t)sq->qbuf_end;
    uintptr_t current = (uintptr_t)sq->qbuf_curr;
    if (sq->idx != jetty->jetty_id.id || sq->baseblk_shift != 6 ||
        !sq->baseblk_cnt || sq->baseblk_cnt > 32768 ||
        (sq->baseblk_cnt & (sq->baseblk_cnt - 1)) ||
        !begin || end <= begin || end - begin != sq->qbuf_size ||
        sq->qbuf_size != (sq->baseblk_cnt << 6) ||
        current != begin + ((sq->pi & (sq->baseblk_cnt - 1)) << 6)) {
        fprintf(stderr, "WQE_LAYOUT_UNKNOWN jetty=%u queue=%u shift=%u blocks=%u\n",
                jetty->jetty_id.id, sq->idx, sq->baseblk_shift, sq->baseblk_cnt);
        return NULL;
    }
    *producer = sq->pi;
    return sq->qbuf_curr;
}

void diag_wqe_after(urma_jetty_t *jetty, void *entry, uint32_t producer,
                    const urma_jfs_wr_t *wr, uint64_t wr_id)
{
    struct udma_u_jetty_queue *sq = &((struct udma_u_jetty *)jetty)->sq;
    if (!entry) return;
    uint32_t blocks = sq->pi - producer;
    if (blocks < 1 || blocks > 4) {
        fprintf(stderr, "WQE_POSITION_UNKNOWN wr_id=%" PRIu64 " blocks=%u\n", wr_id, blocks);
        return;
    }
    unsigned char raw[256] = {0};
    size_t offset = (uintptr_t)entry - (uintptr_t)sq->qbuf;
    for (size_t i = 0; i < blocks * 64; ++i)
        raw[i] = ((const volatile unsigned char *)sq->qbuf)[(offset + i) % sq->qbuf_size];
    struct udma_jfs_sqe_ctl ctl;
    struct udma_wqe_sge sge;
    memcpy(&ctl, raw, sizeof(ctl));
    memcpy(&sge, raw + sizeof(ctl), sizeof(sge));
    uint64_t remote = (uint64_t)ctl.rmt_addr_h_or_token_value << 32 |
                      ctl.rmt_addr_l_or_token_id;
    uint64_t first = 0, second = 0;
    memcpy(&first, raw + SQE_ATOMIC_DATA_FIELD, sizeof(first));
    memcpy(&second, raw + SQE_ATOMIC_DATA_FIELD + 8, sizeof(second));
    fprintf(stderr, "WQE wr_id=%" PRIu64 " opcode=%u flags=%u remote=%" PRIx64
            " seg_id=%u local=%" PRIx64 " length=%u operand0=%" PRIu64
            " operand1=%" PRIu64 " blocks=%u pi=%u->%u\n",
            wr_id, ctl.opcode, ctl.flag, remote, ctl.rmt_jetty_or_seg_id,
            sge.va, sge.length, first, second, blocks, producer, sq->pi);
    int match = -1;
    if (wr->opcode == URMA_OPC_FADD || wr->opcode == URMA_OPC_CAS) {
        const urma_sge_t *src = wr->opcode == URMA_OPC_FADD ? wr->faa.src : wr->cas.src;
        const urma_sge_t *dst = wr->opcode == URMA_OPC_FADD ? wr->faa.dst : wr->cas.dst;
        uint32_t opcode = wr->opcode == URMA_OPC_FADD ? UDMA_OPCODE_FAA : UDMA_OPCODE_CAS;
        match = ctl.opcode == opcode && ctl.flag == (wr->flag.value & 0x7f) &&
                remote == dst->addr && sge.va == src->addr && sge.length == src->len &&
                src->len == 8 && dst->len == 8 && blocks == UDMA_ATOMIC_WQE_BB_NUM &&
                first == (wr->opcode == URMA_OPC_FADD ? wr->faa.operand : wr->cas.swap_data) &&
                (wr->opcode != URMA_OPC_CAS || second == wr->cas.cmp_data);
    }
    fprintf(stderr, "WQE_ATOMIC_FIELDS_MATCH wr_id=%" PRIu64 " match=%d\n", wr_id, match);
    fprintf(stderr, "WQE_RAW wr_id=%" PRIu64 " bytes=", wr_id);
    for (size_t i = 0; i < blocks * 64; ++i) fprintf(stderr, "%02x", raw[i]);
    fputc('\n', stderr);
}
