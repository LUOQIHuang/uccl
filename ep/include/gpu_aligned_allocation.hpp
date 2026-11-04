#pragma once
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace uccl::gpu_p2p {
inline size_t aligned_allocation_size(size_t bytes, size_t alignment) {
  if (!bytes || !alignment || (alignment & (alignment - 1)))
    throw std::invalid_argument("invalid GPU allocation size/alignment");
  size_t const max = std::numeric_limits<size_t>::max();
  if (bytes > max - (alignment - 1))
    throw std::overflow_error("GPU allocation size overflow");
  size_t rounded = (bytes + alignment - 1) & ~(alignment - 1);
  if (rounded > max - alignment)
    throw std::overflow_error("GPU allocation padding overflow");
  return rounded + alignment;
}
inline void* aligned_allocation_view(void* raw, size_t alignment) {
  if (!raw || !alignment || (alignment & (alignment - 1)))
    throw std::invalid_argument("invalid GPU allocation pointer/alignment");
  uintptr_t address = reinterpret_cast<uintptr_t>(raw);
  if (address > std::numeric_limits<uintptr_t>::max() - (alignment - 1))
    throw std::overflow_error("GPU allocation address overflow");
  return reinterpret_cast<void*>((address + alignment - 1) & ~(uintptr_t(alignment) - 1));
}
}  // namespace uccl::gpu_p2p
