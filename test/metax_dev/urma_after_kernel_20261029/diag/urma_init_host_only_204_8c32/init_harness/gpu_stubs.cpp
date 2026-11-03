#include "gpu_p2p_runtime.hpp"
#include <cstdlib>
#include <unistd.h>
namespace uccl::gpu_p2p {
char const* vendor_name() { return "host-init-only"; }
std::size_t registration_alignment() { return static_cast<std::size_t>(sysconf(_SC_PAGESIZE)); }
bool atomic_buffer_is_gpu_memory() { std::abort(); }
void prepare_main_buffer(void*, std::size_t, int) { std::abort(); }
void prepare_atomic_buffer(void*, std::size_t, int) { std::abort(); }
void release_memory(void*, MemoryKind, int) { std::abort(); }
}
