#include <urma_api.h>

#include <cerrno>
#include <cinttypes>
#include <cstdio>
#include <dlfcn.h>
#include <pthread.h>
#include <sys/syscall.h>
#include <unistd.h>

using Register = urma_target_seg_t* (*)(urma_context_t*, urma_seg_cfg_t*);
static Register real_register;
static pthread_once_t resolve_once = PTHREAD_ONCE_INIT;

static void resolve_real_register() {
  constexpr char const* path = "/home/f00955680/metax_dev/urma_after_kernel_20261029/diag/umdk_private_618957c3/lib/liburma.so.0.0.3";
  void* lib = dlopen(path, RTLD_NOW | RTLD_NOLOAD);
  if (lib) real_register = reinterpret_cast<Register>(dlsym(lib, "urma_register_seg"));
}

extern "C" urma_target_seg_t* urma_register_seg(urma_context_t* ctx, urma_seg_cfg_t* cfg) {
  pthread_once(&resolve_once, resolve_real_register);
  if (!real_register || !cfg) {
    std::fprintf(stderr, "URMA_TRACE_ERROR real=%p cfg=%p\n", reinterpret_cast<void*>(real_register), static_cast<void*>(cfg));
    errno = ENOSYS;
    return nullptr;
  }
  int before = errno;
  auto tid = syscall(SYS_gettid);
  std::fprintf(stderr, "URMA_TRACE_BEGIN tid=%ld va=0x%" PRIx64 " mod4096=%" PRIu64 " len=%" PRIu64 " access=%u gpu=%u\n",
               tid, cfg->va, cfg->va % 4096, cfg->len, cfg->flag.bs.access, cfg->is_gpu_seg);
  errno = before;
  auto* seg = real_register(ctx, cfg);
  int after = errno;
  std::fprintf(stderr, "URMA_TRACE_DONE tid=%ld va=0x%" PRIx64 " access=%u ok=%d errno=%d\n",
               tid, cfg->va, cfg->flag.bs.access, seg != nullptr, after);
  errno = after;
  return seg;
}
