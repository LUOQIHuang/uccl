#define _GNU_SOURCE
#include <infiniband/verbs.h>
#include <dlfcn.h>
#include <pthread.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

/* Process-local observation only. The original WRs, return values, and CQ
 * entries are not modified. Context operations are hooked before the newly
 * opened context is returned to the caller. */
struct slot {
  struct ibv_context *ctx;
  int (*post)(struct ibv_qp *, struct ibv_send_wr *, struct ibv_send_wr **);
  int (*poll)(struct ibv_cq *, int, struct ibv_wc *);
};
static struct slot slots[64];
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned post_count, cq_count;

static struct slot get_slot(struct ibv_context *ctx) {
  struct slot found = {0};
  pthread_mutex_lock(&lock);
  for (unsigned i = 0; i < 64; ++i)
    if (slots[i].ctx == ctx) { found = slots[i]; break; }
  pthread_mutex_unlock(&lock);
  if (!found.ctx) { fprintf(stderr, "VERBS_TRACE_MISSING_CONTEXT\n"); abort(); }
  return found;
}

static int observe_post(struct ibv_qp *qp, struct ibv_send_wr *wr,
                        struct ibv_send_wr **bad) {
  struct slot slot = get_slot(qp->context);
  int rc = slot.post(qp, wr, bad);
  pthread_mutex_lock(&lock);
  for (struct ibv_send_wr *it = wr; it && post_count < 16; it = it->next) {
    if (it->opcode != IBV_WR_ATOMIC_FETCH_AND_ADD) continue;
    ++post_count;
    fprintf(stderr, "VERBS_TRACE_POST pid=%ld qp=%u wrid=%" PRIu64
            " remote=0x%" PRIx64 " add=%" PRIu64 " flags=%u post_rc=%d\n",
            (long)getpid(), qp->qp_num, it->wr_id, it->wr.atomic.remote_addr,
            it->wr.atomic.compare_add, it->send_flags, rc);
  }
  pthread_mutex_unlock(&lock);
  return rc;
}

static int observe_poll(struct ibv_cq *cq, int count, struct ibv_wc *wc) {
  struct slot slot = get_slot(cq->context);
  int n = slot.poll(cq, count, wc);
  pthread_mutex_lock(&lock);
  for (int i = 0; i < n && cq_count < 16; ++i) {
    if (wc[i].opcode != IBV_WC_FETCH_ADD) continue;
    ++cq_count;
    fprintf(stderr, "VERBS_TRACE_CQ pid=%ld qp=%u wrid=%" PRIu64 " status=%u\n",
            (long)getpid(), wc[i].qp_num, wc[i].wr_id, wc[i].status);
  }
  pthread_mutex_unlock(&lock);
  return n;
}

void verbs_trace_install(struct ibv_context *ctx) {
  pthread_mutex_lock(&lock);
  unsigned i;
  for (i = 0; i < 64 && slots[i].ctx; ++i) {}
  if (i == 64 || !ctx->ops.post_send || !ctx->ops.poll_cq) {
    fprintf(stderr, "VERBS_TRACE_INSTALL_FAILED\n"); abort();
  }
  slots[i] = (struct slot){ctx, ctx->ops.post_send, ctx->ops.poll_cq};
  ctx->ops.post_send = observe_post;
  ctx->ops.poll_cq = observe_poll;
  pthread_mutex_unlock(&lock);
  fprintf(stderr, "VERBS_TRACE_INSTALL pid=%ld ctx=%p\n", (long)getpid(), (void *)ctx);
}

struct ibv_context *ibv_open_device(struct ibv_device *device) {
  typedef struct ibv_context *(*open_fn)(struct ibv_device *);
  open_fn real_open = (open_fn)dlsym(RTLD_NEXT, "ibv_open_device");
  if (!real_open) { fprintf(stderr, "VERBS_TRACE_OPEN_LOOKUP_FAILED\n"); abort(); }
  struct ibv_context *ctx = real_open(device);
  if (ctx) verbs_trace_install(ctx);
  return ctx;
}

int ibv_close_device(struct ibv_context *ctx) {
  typedef int (*close_fn)(struct ibv_context *);
  close_fn real_close = (close_fn)dlsym(RTLD_NEXT, "ibv_close_device");
  if (!real_close) { fprintf(stderr, "VERBS_TRACE_CLOSE_LOOKUP_FAILED\n"); abort(); }
  pthread_mutex_lock(&lock);
  for (unsigned i = 0; i < 64; ++i) if (slots[i].ctx == ctx) {
    ctx->ops.post_send = slots[i].post;
    ctx->ops.poll_cq = slots[i].poll;
    slots[i] = (struct slot){0};
    break;
  }
  pthread_mutex_unlock(&lock);
  return real_close(ctx);
}
