#include <urma_api.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

namespace {
using Post = urma_status_t (*)(urma_jetty_t*, urma_jfs_wr_t*, urma_jfs_wr_t**);
using Poll = int (*)(urma_jfc_t*, int, urma_cr_t*);
Post real_post = nullptr;
Poll real_poll = nullptr;
pthread_once_t resolve_once = PTHREAD_ONCE_INIT;

void resolve_symbols() {
  constexpr auto path = "/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib/liburma.so.0.0.3";
  void* library = dlopen(path, RTLD_NOW | RTLD_NOLOAD);
  if (!library) return;
  real_post = reinterpret_cast<Post>(dlsym(library, "urma_post_jetty_send_wr"));
  real_poll = reinterpret_cast<Poll>(dlsym(library, "urma_poll_jfc"));
}

uint64_t now_ns() {
  timespec value{};
  clock_gettime(CLOCK_MONOTONIC, &value);
  return static_cast<uint64_t>(value.tv_sec) * 1000000000ULL + value.tv_nsec;
}

struct Stats {
  uint64_t post_calls = 0, write_calls = 0, fadd_calls = 0, other_calls = 0;
  uint64_t write_bytes = 0, write_small = 0, write_medium = 0, write_large = 0;
  uint64_t post_errors = 0, post_samples = 0, post_sample_ns = 0;
  uint64_t polls = 0, empty_polls = 0, completions = 0, poll_errors = 0;
  uint64_t poll_samples = 0, poll_sample_ns = 0;
  ~Stats() {
    if (!post_calls && !polls) return;
    std::fprintf(stderr,
        "URMA_OP_STATS pid=%d tid=%ld post=%llu write=%llu fadd=%llu other=%llu "
        "bytes=%llu write_le_4k=%llu write_le_64k=%llu write_gt_64k=%llu post_err=%llu "
        "post_samples=%llu post_ns=%llu polls=%llu empty=%llu completions=%llu "
        "poll_err=%llu poll_samples=%llu poll_ns=%llu\n",
        getpid(), syscall(SYS_gettid),
        static_cast<unsigned long long>(post_calls), static_cast<unsigned long long>(write_calls),
        static_cast<unsigned long long>(fadd_calls), static_cast<unsigned long long>(other_calls),
        static_cast<unsigned long long>(write_bytes), static_cast<unsigned long long>(write_small),
        static_cast<unsigned long long>(write_medium), static_cast<unsigned long long>(write_large),
        static_cast<unsigned long long>(post_errors), static_cast<unsigned long long>(post_samples),
        static_cast<unsigned long long>(post_sample_ns), static_cast<unsigned long long>(polls),
        static_cast<unsigned long long>(empty_polls), static_cast<unsigned long long>(completions),
        static_cast<unsigned long long>(poll_errors), static_cast<unsigned long long>(poll_samples),
        static_cast<unsigned long long>(poll_sample_ns));
  }
};
thread_local Stats stats;
}  // namespace

extern "C" urma_status_t urma_post_jetty_send_wr(urma_jetty_t* jetty,
                                                 urma_jfs_wr_t* wr,
                                                 urma_jfs_wr_t** bad) {
  pthread_once(&resolve_once, resolve_symbols);
  if (!real_post || !wr) { errno = ENOSYS; return URMA_EINVAL; }
  auto& state = stats;
  ++state.post_calls;
  if (wr->opcode == URMA_OPC_WRITE) {
    ++state.write_calls;
    uint64_t bytes = 0;
    for (uint32_t i = 0; i < wr->rw.src.num_sge; ++i) bytes += wr->rw.src.sge[i].len;
    state.write_bytes += bytes;
    if (bytes <= 4096) ++state.write_small;
    else if (bytes <= 65536) ++state.write_medium;
    else ++state.write_large;
  } else if (wr->opcode == URMA_OPC_FADD) ++state.fadd_calls;
  else ++state.other_calls;
  bool const sample = (state.post_calls & 255U) == 1;
  uint64_t const before = sample ? now_ns() : 0;
  auto const rc = real_post(jetty, wr, bad);
  int const saved_errno = errno;
  if (sample) { ++state.post_samples; state.post_sample_ns += now_ns() - before; }
  if (rc != URMA_SUCCESS) ++state.post_errors;
  errno = saved_errno;
  return rc;
}

extern "C" int urma_poll_jfc(urma_jfc_t* jfc, int count, urma_cr_t* cr) {
  pthread_once(&resolve_once, resolve_symbols);
  if (!real_poll) { errno = ENOSYS; return -1; }
  auto& state = stats;
  ++state.polls;
  bool const sample = (state.polls & 1023U) == 1;
  uint64_t const before = sample ? now_ns() : 0;
  int const rc = real_poll(jfc, count, cr);
  int const saved_errno = errno;
  if (sample) { ++state.poll_samples; state.poll_sample_ns += now_ns() - before; }
  if (rc < 0) ++state.poll_errors;
  else if (rc == 0) ++state.empty_polls;
  else state.completions += static_cast<uint64_t>(rc);
  errno = saved_errno;
  return rc;
}
