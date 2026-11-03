#include <urma_api.h>

#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unistd.h>
#include <vector>

struct Case {
  const char* name;
  void* address;
  size_t length;
  bool atomic;
};

static bool run_case(urma_context_t* context, size_t page, const Case& c) {
  urma_token_id_t* token = urma_alloc_token_id(context);
  if (!token) {
    std::printf("CASE=%s TOKEN_ALLOC_FAILED\n", c.name);
    return false;
  }
  urma_seg_cfg_t cfg{};
  cfg.va = reinterpret_cast<uint64_t>(c.address);
  cfg.len = c.length;
  cfg.token_id = token;
  cfg.flag.bs.token_id_valid = URMA_TOKEN_ID_VALID;
  cfg.flag.bs.token_policy = URMA_TOKEN_NONE;
  cfg.flag.bs.cacheable = URMA_NON_CACHEABLE;
  cfg.flag.bs.non_pin = 0;
  cfg.flag.bs.access = URMA_ACCESS_READ | URMA_ACCESS_WRITE;
  if (c.atomic) cfg.flag.bs.access |= URMA_ACCESS_ATOMIC;
  cfg.is_gpu_seg = 0;
  errno = 0;
  urma_target_seg_t* segment = urma_register_seg(context, &cfg);
  int saved_errno = errno;
  std::printf("CASE=%s addr=%p page_offset=%zu len=%zu atomic=%d registered=%d errno=%d\n",
              c.name, c.address,
              static_cast<size_t>(reinterpret_cast<uintptr_t>(c.address) % page),
              c.length, int(c.atomic), int(segment != nullptr), saved_errno);
  std::fflush(stdout);
  if (segment) {
    int rc = urma_unregister_seg(segment);
    std::printf("CASE=%s unregister_rc=%d\n", c.name, rc);
    std::fflush(stdout);
    if (rc != URMA_SUCCESS) {
      std::fprintf(stderr, "UNREGISTER_FAILED_STOPPING\n");
      std::fflush(stderr);
      std::_Exit(66);
    }
  }
  int token_rc = urma_free_token_id(token);
  std::printf("CASE=%s free_token_rc=%d\n", c.name, token_rc);
  std::fflush(stdout);
  return token_rc == URMA_SUCCESS;
}

int main() {
  setvbuf(stdout, nullptr, _IOLBF, 0);
  long page_raw = sysconf(_SC_PAGESIZE);
  if (page_raw <= 0) return 10;
  size_t page = static_cast<size_t>(page_raw);
  std::printf("PAGE_SIZE=%zu\n", page);
  std::vector<uint64_t> operands(1024, 0);
  std::vector<uint64_t> barrier(8, 0);
  void* operands_aligned = nullptr;
  void* barrier_aligned = nullptr;
  size_t operand_bytes = operands.size() * sizeof(uint64_t);
  size_t barrier_bytes = barrier.size() * sizeof(uint64_t);
  size_t alloc_8192 = ((operand_bytes + 8 + page - 1) / page) * page;
  size_t alloc_64 = ((barrier_bytes + 8 + page - 1) / page) * page;
  if (posix_memalign(&operands_aligned, page, alloc_8192) != 0 ||
      posix_memalign(&barrier_aligned, page, alloc_64) != 0) return 11;
  std::memset(operands_aligned, 0, alloc_8192);
  std::memset(barrier_aligned, 0, alloc_64);
  int init_rc = urma_init(nullptr);
  std::printf("URMA_INIT_RC=%d\n", init_rc);
  if (init_rc != URMA_SUCCESS) return 12;
  int count = 0;
  urma_device_t** devices = urma_get_device_list(&count);
  urma_device_t* device = nullptr;
  for (int i = 0; devices && i < count; ++i)
    if (devices[i] && std::strcmp(devices[i]->name, "udmac0d1e2") == 0)
      device = devices[i];
  if (!device) return 13;
  uint32_t eid_count = 0;
  urma_eid_info_t* eids = urma_get_eid_list(device, &eid_count);
  if (!eids || !eid_count) return 14;
  urma_context_t* context = urma_create_context(device, eids[0].eid_index);
  if (!context) return 15;
  std::printf("DEVICE=%s EID_INDEX=%u\n", device->name, eids[0].eid_index);
  const Case cases[] = {
      {"vector_8192_rw", operands.data(), operand_bytes, false},
      {"aligned_8192_rw", operands_aligned, operand_bytes, false},
      {"offset8_8192_rw", static_cast<char*>(operands_aligned) + 8, operand_bytes, false},
      {"vector_64_rw_atomic", barrier.data(), barrier_bytes, true},
      {"aligned_64_rw_atomic", barrier_aligned, barrier_bytes, true},
      {"offset8_64_rw_atomic", static_cast<char*>(barrier_aligned) + 8, barrier_bytes, true},
  };
  bool cleanup_ok = true;
  for (const Case& c : cases) {
    if (!run_case(context, page, c)) {
      cleanup_ok = false;
      break;
    }
  }
  int delete_rc = urma_delete_context(context);
  urma_free_eid_list(eids);
  urma_free_device_list(devices);
  int uninit_rc = urma_uninit();
  std::free(operands_aligned);
  std::free(barrier_aligned);
  std::printf("FINAL cleanup_ok=%d delete_context_rc=%d uninit_rc=%d\n",
              int(cleanup_ok), delete_rc, uninit_rc);
  return cleanup_ok && delete_rc == URMA_SUCCESS && uninit_rc == URMA_SUCCESS ? 0 : 16;
}
