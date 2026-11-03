#include <mcr/mc_runtime_types.h>

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <dlfcn.h>
#include <limits>

using SetDevice = int (*)(int);
using Malloc = int (*)(void**, size_t);
using ExtMalloc = int (*)(void**, size_t, unsigned int);
using Memset = int (*)(void*, int, size_t);
using Synchronize = int (*)();
using Free = int (*)(void*);
using Clock = std::chrono::steady_clock;

static void end_phase(const char* name, Clock::time_point begin, int rc) {
  double seconds = std::chrono::duration<double>(Clock::now() - begin).count();
  std::printf("PHASE=%s_DONE RC=%d ELAPSED_SECONDS=%.6f\n", name, rc, seconds);
}

int main(int argc, char** argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  if (argc == 2 && !std::strcmp(argv[1], "--inspect")) {
    std::printf("FINEGRAINED_FLAG=0x%x NO_GPU_CALLS=1\n", mcDeviceMallocFinegrained);
    return 0;
  }
  if (argc != 3 || (std::strcmp(argv[1], "normal") && std::strcmp(argv[1], "fine"))) {
    std::fprintf(stderr, "usage: probe normal|fine logical_bytes\n");
    return 2;
  }
  char* end = nullptr;
  unsigned long long parsed = std::strtoull(argv[2], &end, 10);
  if (!end || *end || parsed == 0 || parsed > 536870912ULL ||
      parsed > std::numeric_limits<size_t>::max() - 4095) return 3;
  size_t logical = static_cast<size_t>(parsed);
  size_t backing = (logical + 4095) & ~static_cast<size_t>(4095);
  bool fine = !std::strcmp(argv[1], "fine");
  std::printf("MODE=%s LOGICAL_BYTES=%zu BACKING_BYTES=%zu FINE_FLAG=0x%x\n",
              argv[1], logical, backing, mcDeviceMallocFinegrained);
  auto total = Clock::now();
  auto t = Clock::now();
  std::puts("PHASE=RUNTIME_LOAD_BEGIN");
  void* lib = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_GLOBAL);
  if (!lib) { std::printf("RUNTIME_LOAD_FAILED=%s\n", dlerror()); return 10; }
  end_phase("RUNTIME_LOAD", t, 0);
  auto set = reinterpret_cast<SetDevice>(dlsym(lib, "mcSetDevice"));
  auto alloc = reinterpret_cast<Malloc>(dlsym(lib, "mcMalloc"));
  auto ext_alloc = reinterpret_cast<ExtMalloc>(dlsym(lib, "mcExtMallocWithFlags"));
  auto clear = reinterpret_cast<Memset>(dlsym(lib, "mcMemset"));
  auto sync = reinterpret_cast<Synchronize>(dlsym(lib, "mcDeviceSynchronize"));
  auto release = reinterpret_cast<Free>(dlsym(lib, "mcFree"));
  if (!set || !alloc || !ext_alloc || !clear || !sync || !release) {
    std::printf("SYMBOL_MISSING set=%d malloc=%d ext_malloc=%d memset=%d sync=%d free=%d\n",
                !!set, !!alloc, !!ext_alloc, !!clear, !!sync, !!release);
    return 11;
  }
  t = Clock::now();
  std::puts("PHASE=SET_DEVICE_BEGIN");
  int rc = set(0);
  end_phase("SET_DEVICE", t, rc);
  if (rc) return 12;
  void* ptr = nullptr;
  t = Clock::now();
  std::puts("PHASE=ALLOC_BEGIN");
  rc = fine ? ext_alloc(&ptr, backing, mcDeviceMallocFinegrained) : alloc(&ptr, backing);
  end_phase("ALLOC", t, rc);
  if (rc || !ptr) return 13;
  t = Clock::now();
  std::puts("PHASE=MEMSET_BEGIN");
  rc = clear(ptr, 0, logical);
  end_phase("MEMSET", t, rc);
  if (rc) { release(ptr); return 14; }
  t = Clock::now();
  std::puts("PHASE=SYNC_BEGIN");
  rc = sync();
  end_phase("SYNC", t, rc);
  if (rc) { release(ptr); return 15; }
  t = Clock::now();
  std::puts("PHASE=FREE_BEGIN");
  rc = release(ptr);
  end_phase("FREE", t, rc);
  if (rc) return 16;
  end_phase("TOTAL", total, 0);
  std::printf("PROBE_OK MODE=%s LOGICAL_BYTES=%zu\n", argv[1], logical);
  return 0;
}
