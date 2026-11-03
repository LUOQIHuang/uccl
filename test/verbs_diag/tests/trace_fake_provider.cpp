// CPU-only provider: never accesses a GPU, NIC or the supplied data addresses.
#include <urma_api.h>
#include <cerrno>
#include <deque>
thread_local std::deque<uint64_t> pending;
extern "C" urma_status_t urma_post_jetty_send_wr(urma_jetty_t*, urma_jfs_wr_t* wr, urma_jfs_wr_t**) {
  for (auto* p=wr; p; p=p->next) pending.push_back(p->user_ctx);
  errno=EAGAIN; return URMA_SUCCESS;
}
extern "C" int urma_poll_jfc(urma_jfc_t*, int count, urma_cr_t* cr) {
  int n=0;
  while (n<count && !pending.empty()) {
    cr[n]={}; cr[n].user_ctx=pending.front(); cr[n].status=URMA_CR_SUCCESS;
    pending.pop_front(); ++n;
  }
  errno=EAGAIN; return n;
}
extern "C" urma_target_seg_t* urma_register_seg(urma_context_t*, urma_seg_cfg_t*) {
  static urma_target_seg_t segment{}; return &segment;
}
