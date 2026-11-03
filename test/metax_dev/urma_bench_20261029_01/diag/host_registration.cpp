#include <urma_api.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>

struct Case {
  const char* name;
  void* address;
  size_t length;
  bool explicit_token;
  bool atomic;
};

int main() {
  int rc = urma_init(nullptr);
  std::printf("urma_init=%d\n", rc);
  if (rc != URMA_SUCCESS) return 10;
  int count = 0;
  urma_device_t** devices = urma_get_device_list(&count);
  if (!devices || count <= 0) return 11;
  urma_device_t* device = nullptr;
  for (int i = 0; i < count; ++i) {
    if (devices[i] && std::strcmp(devices[i]->name, "udmac0d1e2") == 0)
      device = devices[i];
  }
  if (!device) return 12;
  uint32_t eid_count = 0;
  urma_eid_info_t* eids = urma_get_eid_list(device, &eid_count);
  if (!eids || !eid_count) return 13;
  urma_context_t* context = urma_create_context(device, eids[0].eid_index);
  if (!context) return 14;
  std::printf("device=%s devices=%d eid_index=%u page_size=%ld\n", device->name,
              count, eids[0].eid_index, sysconf(_SC_PAGESIZE));
  long page = sysconf(_SC_PAGESIZE);
  if (page <= 0) return 15;
  void* aligned = nullptr;
  if (posix_memalign(&aligned, static_cast<size_t>(page),
                     static_cast<size_t>(page) * 2) != 0)
    return 16;
  void* heap = std::malloc(64);
  if (!heap) return 17;
  const Case cases[] = {
      {"heap_64_token_rw", heap, 64, true, false},
      {"aligned_64_token_rw", aligned, 64, true, false},
      {"aligned_page_token_rw", aligned, static_cast<size_t>(page), true, false},
      {"aligned_page_no_token_rw", aligned, static_cast<size_t>(page), false, false},
      {"aligned_page_no_token_atomic", aligned, static_cast<size_t>(page), false, true},
      {"aligned_page_token_atomic", aligned, static_cast<size_t>(page), true, true},
  };
  int passed = 0;
  for (const auto& test : cases) {
    urma_token_id_t* token = nullptr;
    if (test.explicit_token) {
      token = urma_alloc_token_id(context);
      if (!token) {
        std::printf("CASE=%s TOKEN_ALLOC_FAILED errno=%d\n", test.name, errno);
        continue;
      }
    }
    urma_seg_cfg_t cfg{};
    cfg.va = reinterpret_cast<uint64_t>(test.address);
    cfg.len = test.length;
    cfg.token_id = token;
    if (token) cfg.flag.bs.token_id_valid = URMA_TOKEN_ID_VALID;
    cfg.flag.bs.token_policy = URMA_TOKEN_NONE;
    cfg.flag.bs.cacheable = URMA_NON_CACHEABLE;
    cfg.flag.bs.non_pin = 0;
    cfg.flag.bs.access = URMA_ACCESS_READ | URMA_ACCESS_WRITE;
    if (test.atomic) cfg.flag.bs.access |= URMA_ACCESS_ATOMIC;
    cfg.is_gpu_seg = 0;
    errno = 0;
    urma_target_seg_t* seg = urma_register_seg(context, &cfg);
    int saved_errno = errno;
    std::printf("CASE=%s len=%zu token=%d atomic=%d result=%s errno=%d (%s)\n",
                test.name, test.length, int(test.explicit_token), int(test.atomic),
                seg ? "OK" : "FAILED", saved_errno, std::strerror(saved_errno));
    if (seg) {
      int unreg = urma_unregister_seg(seg);
      std::printf("CASE=%s unregister=%d\n", test.name, unreg);
      if (unreg == URMA_SUCCESS) ++passed;
    }
    if (token) {
      int free_rc = urma_free_token_id(token);
      std::printf("CASE=%s free_token=%d\n", test.name, free_rc);
    }
  }
  std::free(heap);
  std::free(aligned);
  int context_rc = urma_delete_context(context);
  urma_free_eid_list(eids);
  urma_free_device_list(devices);
  int uninit_rc = urma_uninit();
  std::printf("SUMMARY registered_and_unregistered=%d/%zu delete_context=%d uninit=%d\n",
              passed, sizeof(cases) / sizeof(cases[0]), context_rc, uninit_rc);
  return context_rc == URMA_SUCCESS && uninit_rc == URMA_SUCCESS ? 0 : 18;
}
