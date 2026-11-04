// Compile the actual production allocation function with a fine-grained
// allocator returning a deliberately misaligned address.
#include "urma_transport.hpp"
#include "gpu_p2p_runtime.hpp"
#include "gpu_aligned_allocation.hpp"
#include <cassert>
#include <cstdio>
#include <memory>
#include <string>
#include <utility>

constexpr size_t kAtomicBufferSize = 81960;
constexpr int mcDeviceMallocFinegrained = 7;
alignas(65536) unsigned char storage[98304];
void* raw = storage + 0x600;
int frees = 0;
bool fail_allocate = false, fail_owner = false;
int mcExtMallocWithFlags(void** out, size_t bytes, int flags) {
  assert(flags == mcDeviceMallocFinegrained && bytes == 90112);
  *out = fail_allocate ? nullptr : raw;
  return fail_allocate ? 1 : 0;
}
namespace uccl::gpu_p2p {
void set_device(int device) { assert(device == 0); }
size_t registration_alignment() { return 4096; }
void release_memory(void* ptr, MemoryKind kind, int device) {
  assert(ptr == raw && kind == MemoryKind::Device && device == 0);
  ++frees;
}
}
std::shared_ptr<void> UrmaTransport::acquire_buffer_ownership(
    void* view, bool host, int device, void* base) {
  assert(view == storage + 4096 && !host && device == 0 && base == raw);
  if (fail_owner) throw std::runtime_error("owner failure");
  return std::shared_ptr<void>(raw, [](void* ptr) {
    uccl::gpu_p2p::release_memory(ptr, uccl::gpu_p2p::MemoryKind::Device, 0);
  });
}
#define __MC_PLATFORM_MXCC__ 1
#include "atomic_allocation.inc"
int main() {
  auto allocation = allocate_urma_atomic_control(0);
  assert(allocation.first == storage + 4096 && frees == 0);
  allocation.second.reset();
  assert(frees == 1);
  fail_owner = true;
  try { allocate_urma_atomic_control(0); assert(false); }
  catch (std::runtime_error const&) {}
  assert(frees == 2);
  fail_owner = false; fail_allocate = true;
  try { allocate_urma_atomic_control(0); assert(false); }
  catch (std::runtime_error const&) {}
  assert(frees == 2);
  for (size_t offset = 0; offset < 4096; ++offset) {
    void* base = storage + offset;
    auto* view = static_cast<unsigned char*>(uccl::gpu_p2p::aligned_allocation_view(base,4096));
    assert(reinterpret_cast<uintptr_t>(view) % 4096 == 0);
    assert(view + 81960 <= storage + offset + uccl::gpu_p2p::aligned_allocation_size(81960,4096));
  }
  try { uccl::gpu_p2p::aligned_allocation_size(SIZE_MAX,4096); assert(false); }
  catch (std::overflow_error const&) {}
  std::puts("URMA production fine-grained allocation tests passed");
}
