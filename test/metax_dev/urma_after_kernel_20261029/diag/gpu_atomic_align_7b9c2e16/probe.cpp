#include <urma_api.h>
#include <mcr/mc_runtime_types.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>

using SetDevice = int (*)(int);
using ExtMalloc = int (*)(void**, size_t, unsigned int);
using Free = int (*)(void*);
using Memset = int (*)(void*, int, size_t);
using Sync = int (*)();

int main() {
  setvbuf(stdout, nullptr, _IONBF, 0);
  constexpr size_t logical = 81960;
  constexpr size_t padding = 16384;
  void* lib = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_GLOBAL);
  if (!lib) { std::printf("RUNTIME_FAILED=%s\n", dlerror()); return 10; }
  auto set = reinterpret_cast<SetDevice>(dlsym(lib, "mcSetDevice"));
  auto allocate = reinterpret_cast<ExtMalloc>(dlsym(lib, "mcExtMallocWithFlags"));
  auto release = reinterpret_cast<Free>(dlsym(lib, "mcFree"));
  auto clear = reinterpret_cast<Memset>(dlsym(lib, "mcMemset"));
  auto sync = reinterpret_cast<Sync>(dlsym(lib, "mcDeviceSynchronize"));
  if (!set || !allocate || !release || !clear || !sync || set(0)) return 11;
  void* backing = nullptr;
  int rc = allocate(&backing, logical + padding, mcDeviceMallocFinegrained);
  std::printf("ALLOC_RC=%d BACKING=%p BYTES=%zu FLAG=%u\n", rc, backing,
              logical + padding, static_cast<unsigned>(mcDeviceMallocFinegrained));
  if (rc || !backing) return 12;
  auto raw = reinterpret_cast<uintptr_t>(backing);
  auto aligned = (raw + 8191) & ~static_cast<uintptr_t>(8191);
  std::printf("RAW=0x%lx ALIGNED=0x%lx RAW_MOD4096=%lu ALIGNED_MOD8192=%lu\n",
              static_cast<unsigned long>(raw), static_cast<unsigned long>(aligned),
              static_cast<unsigned long>(raw % 4096), static_cast<unsigned long>(aligned % 8192));
  rc = clear(reinterpret_cast<void*>(aligned), 0, logical);
  std::printf("MEMSET_RC=%d\n", rc);
  if (rc || (rc = sync())) { std::printf("SYNC_RC=%d\n", rc); release(backing); return 13; }
  int result = 0;
  bool initialized = false;
  urma_device_t** devices = nullptr;
  urma_eid_info_t* eids = nullptr;
  urma_context_t* ctx = nullptr;
  urma_token_id_t* token = nullptr;
  if (urma_init(nullptr) != URMA_SUCCESS) { result = 14; goto cleanup; }
  initialized = true;
  {
    int count = 0;
    devices = urma_get_device_list(&count);
    urma_device_t* device = nullptr;
    for (int i = 0; devices && i < count; ++i)
      if (devices[i] && std::strcmp(devices[i]->name, "udmac0d1e2") == 0) device = devices[i];
    if (!device) { result = 15; goto cleanup; }
    uint32_t eid_count = 0;
    eids = urma_get_eid_list(device, &eid_count);
    if (!eids || !eid_count) { result = 16; goto cleanup; }
    ctx = urma_create_context(device, eids[0].eid_index);
    if (!ctx) { result = 17; goto cleanup; }
  }
  token = urma_alloc_token_id(ctx);
  if (!token) { result = 18; goto cleanup; }
  {
    const uintptr_t addresses[] = {aligned + 8, aligned};
    for (int i = 0; i < 2; ++i) {
      urma_seg_cfg_t cfg{};
      cfg.va = addresses[i];
      cfg.len = logical;
      cfg.token_id = token;
      cfg.flag.bs.token_id_valid = URMA_TOKEN_ID_VALID;
      cfg.flag.bs.token_policy = URMA_TOKEN_NONE;
      cfg.flag.bs.cacheable = URMA_NON_CACHEABLE;
      cfg.flag.bs.non_pin = 0;
      cfg.flag.bs.access = URMA_ACCESS_READ | URMA_ACCESS_WRITE | URMA_ACCESS_ATOMIC;
      cfg.is_gpu_seg = 1;
      errno = 0;
      std::printf("REGISTER_BEGIN CASE=%s VA=0x%lx MOD4096=%lu LEN=%zu ACCESS=%u\n",
                  i ? "ALIGNED" : "PLUS8", static_cast<unsigned long>(cfg.va),
                  static_cast<unsigned long>(cfg.va % 4096), logical, cfg.flag.bs.access);
      auto* seg = urma_register_seg(ctx, &cfg);
      int saved = errno;
      std::printf("REGISTER_DONE CASE=%s OK=%d ERRNO=%d\n", i ? "ALIGNED" : "PLUS8", !!seg, saved);
      if (seg) {
        rc = urma_unregister_seg(seg);
        std::printf("UNREGISTER CASE=%s RC=%d\n", i ? "ALIGNED" : "PLUS8", rc);
        if (rc) { result = 19; break; }
      }
      if ((!i && seg) || (i && !seg)) result = 20;
    }
  }
cleanup:
  if (token) { rc = urma_free_token_id(token); std::printf("FREE_TOKEN_RC=%d\n", rc); if (rc) result = 21; }
  if (ctx) { rc = urma_delete_context(ctx); std::printf("DELETE_CONTEXT_RC=%d\n", rc); if (rc) result = 22; }
  if (eids) urma_free_eid_list(eids);
  if (devices) urma_free_device_list(devices);
  if (initialized) { rc = urma_uninit(); std::printf("URMA_UNINIT_RC=%d\n", rc); if (rc) result = 23; }
  rc = release(backing);
  std::printf("GPU_FREE_RC=%d RESULT=%d\n", rc, result);
  return rc ? 24 : result;
}
