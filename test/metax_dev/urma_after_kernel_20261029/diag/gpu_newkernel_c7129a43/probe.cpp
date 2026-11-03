#include <cstdio>
#include <cstdlib>
#include <dlfcn.h>

using SetDevice = int (*)(int);
using Malloc = int (*)(void**, size_t);
using Memset = int (*)(void*, int, size_t);
using Synchronize = int (*)();
using Free = int (*)(void*);

int main(int argc, char** argv) {
  setvbuf(stdout, nullptr, _IONBF, 0);
  size_t bytes = argc > 1 ? std::strtoull(argv[1], nullptr, 10) : 65536;
  void* lib = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_GLOBAL);
  if (!lib) { std::printf("RUNTIME_LOAD_FAILED=%s\n", dlerror()); return 10; }
  auto set_device = reinterpret_cast<SetDevice>(dlsym(lib, "mcSetDevice"));
  auto allocate = reinterpret_cast<Malloc>(dlsym(lib, "mcMalloc"));
  auto memset_device = reinterpret_cast<Memset>(dlsym(lib, "mcMemset"));
  auto sync = reinterpret_cast<Synchronize>(dlsym(lib, "mcDeviceSynchronize"));
  auto release = reinterpret_cast<Free>(dlsym(lib, "mcFree"));
  if (!set_device || !allocate || !memset_device || !sync || !release) {
    std::printf("SYMBOLS set=%d malloc=%d memset=%d sync=%d free=%d\n", !!set_device, !!allocate, !!memset_device, !!sync, !!release);
    return 11;
  }
  int rc = set_device(0);
  std::printf("SET_DEVICE_RC=%d\n", rc);
  if (rc) return 12;
  void* ptr = nullptr;
  std::printf("PHASE=MC_MALLOC_BEGIN BYTES=%zu\n", bytes);
  rc = allocate(&ptr, bytes);
  std::printf("PHASE=MC_MALLOC_DONE RC=%d ADDRESS=%p\n", rc, ptr);
  if (rc || !ptr) return 13;
  std::puts("PHASE=MC_MEMSET_BEGIN");
  rc = memset_device(ptr, 0, bytes);
  std::printf("PHASE=MC_MEMSET_DONE RC=%d\n", rc);
  if (rc) { std::printf("MC_FREE_AFTER_ERROR_RC=%d\n", release(ptr)); return 14; }
  std::puts("PHASE=MC_SYNC_BEGIN");
  rc = sync();
  std::printf("PHASE=MC_SYNC_DONE RC=%d\n", rc);
  if (rc) { std::printf("MC_FREE_AFTER_ERROR_RC=%d\n", release(ptr)); return 15; }
  rc = release(ptr);
  std::printf("MC_FREE_RC=%d\n", rc);
  return rc ? 16 : 0;
}
