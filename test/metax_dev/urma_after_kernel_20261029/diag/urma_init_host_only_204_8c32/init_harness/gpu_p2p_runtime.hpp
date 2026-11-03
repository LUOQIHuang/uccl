#pragma once
#include <cstddef>
namespace uccl::gpu_p2p {
enum class MemoryKind { Device, HostMapped };
char const* vendor_name();
std::size_t registration_alignment();
bool atomic_buffer_is_gpu_memory();
void prepare_main_buffer(void*, std::size_t, int);
void prepare_atomic_buffer(void*, std::size_t, int);
void release_memory(void*, MemoryKind, int);
}
