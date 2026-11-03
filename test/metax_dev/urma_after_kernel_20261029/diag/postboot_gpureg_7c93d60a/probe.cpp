#include <urma_api.h>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <dlfcn.h>

using SetDevice = int (*)(int);
using Malloc = int (*)(void**, size_t);
using Free = int (*)(void*);

int main() {
  setvbuf(stdout, nullptr, _IONBF, 0);
  std::puts("PHASE=RUNTIME_LOAD_BEGIN");
  void* lib = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_GLOBAL);
  if (!lib) { std::printf("RUNTIME_LOAD_FAILED=%s\n", dlerror()); return 10; }
  auto set_device = reinterpret_cast<SetDevice>(dlsym(lib, "mcSetDevice"));
  auto allocate = reinterpret_cast<Malloc>(dlsym(lib, "mcMalloc"));
  auto release = reinterpret_cast<Free>(dlsym(lib, "mcFree"));
  if (!set_device || !allocate || !release) { std::puts("RUNTIME_SYMBOL_MISSING"); return 11; }
  int rc = set_device(0);
  std::printf("SET_DEVICE_RC=%d\n", rc);
  if (rc) return 12;
  void* memory = nullptr;
  constexpr size_t bytes = 64 * 1024;
  rc = allocate(&memory, bytes);
  std::printf("GPU_ALLOC_RC=%d ADDRESS=%p BYTES=%zu\n", rc, memory, bytes);
  if (rc || !memory) return 13;

  int result = 0;
  bool urma_initialized = false;
  urma_device_t** devices = nullptr;
  urma_eid_info_t* eids = nullptr;
  urma_context_t* context = nullptr;
  urma_token_id_t* token = nullptr;
  urma_target_seg_t* seg = nullptr;
  std::puts("PHASE=URMA_INIT_BEGIN");
  rc = urma_init(nullptr);
  std::printf("URMA_INIT_RC=%d\n", rc);
  if (rc != URMA_SUCCESS) { result = 14; goto cleanup; }
  urma_initialized = true;
  {
    int count = 0;
    devices = urma_get_device_list(&count);
    urma_device_t* device = nullptr;
    for (int i = 0; devices && i < count; ++i)
      if (devices[i] && std::strcmp(devices[i]->name, "udmac0d1e2") == 0) device = devices[i];
    if (!device) { std::puts("URMA_DEVICE_MISSING"); result = 15; goto cleanup; }
    uint32_t eid_count = 0;
    eids = urma_get_eid_list(device, &eid_count);
    if (!eids || !eid_count) { std::puts("URMA_EID_MISSING"); result = 16; goto cleanup; }
    std::puts("PHASE=URMA_CONTEXT_BEGIN");
    context = urma_create_context(device, eids[0].eid_index);
    if (!context) { std::puts("URMA_CONTEXT_FAILED"); result = 17; goto cleanup; }
    std::puts("PHASE=URMA_CONTEXT_DONE");
  }
  token = urma_alloc_token_id(context);
  if (!token) { std::puts("URMA_TOKEN_FAILED"); result = 18; goto cleanup; }
  {
    urma_seg_cfg_t cfg{};
    cfg.va = reinterpret_cast<uint64_t>(memory);
    cfg.len = bytes;
    cfg.token_id = token;
    cfg.flag.bs.token_id_valid = URMA_TOKEN_ID_VALID;
    cfg.flag.bs.token_policy = URMA_TOKEN_NONE;
    cfg.flag.bs.cacheable = URMA_NON_CACHEABLE;
    cfg.flag.bs.non_pin = 0;
    cfg.flag.bs.access = URMA_ACCESS_READ | URMA_ACCESS_WRITE;
    cfg.is_gpu_seg = 1;
    errno = 0;
    std::puts("PHASE=URMA_GPU_REGISTER_BEGIN");
    seg = urma_register_seg(context, &cfg);
    int saved_errno = errno;
    std::printf("PHASE=URMA_GPU_REGISTER_DONE RESULT=%s ERRNO=%d\n", seg ? "OK" : "FAILED", saved_errno);
    if (!seg) result = 19;
  }
cleanup:
  if (seg) { rc = urma_unregister_seg(seg); std::printf("UNREGISTER_RC=%d\n", rc); if (rc) result = 20; }
  if (token) { rc = urma_free_token_id(token); std::printf("FREE_TOKEN_RC=%d\n", rc); if (rc) result = 21; }
  if (context) { rc = urma_delete_context(context); std::printf("DELETE_CONTEXT_RC=%d\n", rc); if (rc) result = 22; }
  if (eids) urma_free_eid_list(eids);
  if (devices) urma_free_device_list(devices);
  if (urma_initialized) { rc = urma_uninit(); std::printf("URMA_UNINIT_RC=%d\n", rc); if (rc) result = 23; }
  rc = release(memory);
  std::printf("GPU_FREE_RC=%d\n", rc);
  if (rc) result = 24;
  std::printf("GPU_PROBE_RESULT=%d\n", result);
  return result;
}
