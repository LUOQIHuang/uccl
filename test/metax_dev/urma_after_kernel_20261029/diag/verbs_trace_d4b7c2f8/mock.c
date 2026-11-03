#include <infiniband/verbs.h>
#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>
static int posts, polls;
static int post(struct ibv_qp *q, struct ibv_send_wr *w, struct ibv_send_wr **b) {
  (void)q; (void)b; ++posts;
  assert(w->opcode == IBV_WR_ATOMIC_FETCH_AND_ADD && w->wr.atomic.remote_addr == 0x1000);
  return 0;
}
static int poll(struct ibv_cq *c, int n, struct ibv_wc *w) {
  (void)c; assert(n >= 1); ++polls;
  memset(w, 0, sizeof(*w)); w->opcode = IBV_WC_FETCH_ADD; w->qp_num = 42; w->wr_id = 17;
  return 1;
}
int main(int argc, char **argv) {
  assert(argc == 2);
  void *lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL); assert(lib);
  void (*install)(struct ibv_context *) = dlsym(lib, "verbs_trace_install"); assert(install);
  struct ibv_context ctx = {0}; ctx.ops.post_send = post; ctx.ops.poll_cq = poll; install(&ctx);
  struct ibv_qp qp = {0}; qp.context = &ctx; qp.qp_num = 42;
  struct ibv_cq cq = {0}; cq.context = &ctx;
  struct ibv_send_wr wr = {0}; wr.opcode = IBV_WR_ATOMIC_FETCH_AND_ADD;
  wr.wr_id = 17; wr.wr.atomic.remote_addr = 0x1000; wr.wr.atomic.compare_add = 1;
  struct ibv_send_wr *bad = NULL; struct ibv_wc wc;
  assert(ibv_post_send(&qp, &wr, &bad) == 0 && ibv_poll_cq(&cq, 1, &wc) == 1);
  assert(posts == 1 && polls == 1 && wc.qp_num == 42 && wc.wr_id == 17);
  puts("VERBS_TRACE_MOCK_PASS"); return 0;
}
