#include "urma_transport.hpp"
#include <cstdio>
#include <exception>
#include <unistd.h>

extern "C" urma_target_seg_t* __real_urma_register_seg(urma_context_t*, urma_seg_cfg_t*);
extern "C" urma_target_seg_t* __wrap_urma_register_seg(urma_context_t* ctx, urma_seg_cfg_t* cfg) {
  auto* result = __real_urma_register_seg(ctx, cfg);
  long page = sysconf(_SC_PAGESIZE);
  std::printf("REGISTER len=%llu page_offset=%llu access=%u gpu=%u success=%d\n",
              static_cast<unsigned long long>(cfg->len),
              static_cast<unsigned long long>(cfg->va % static_cast<uint64_t>(page)),
              static_cast<unsigned>(cfg->flag.bs.access),
              static_cast<unsigned>(cfg->is_gpu_seg), int(result != nullptr));
  std::fflush(stdout);
  return result;
}

int main() {
  setvbuf(stdout, nullptr, _IOLBF, 0);
  ProxyCtx ctx{};
  try {
    UrmaTransport transport;
    transport.init(ctx);
    for (std::size_t i = 0; i < 8; ++i)
      if (transport.load_barrier_word(i) != 0) return 20;
    std::printf("URMA_INIT_SUCCESS barrier_zero=1\n");
    transport.destroy();
    std::printf("URMA_DESTROY_SUCCESS\n");
    return 0;
  } catch (std::exception const& e) {
    std::printf("URMA_INIT_EXCEPTION=%s\n", e.what());
    return 42;
  }
}
