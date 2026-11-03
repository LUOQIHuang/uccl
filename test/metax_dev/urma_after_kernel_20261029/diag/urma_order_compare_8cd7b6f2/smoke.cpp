// Two-node MetaX production-transport smoke. No perftest or mock provider.
#include "gpu_p2p_runtime.hpp"
#include "urma_transport.hpp"
#include <cuda_runtime.h>

extern "C" int gpu_acquire_control(uint64_t*, uint64_t*);

#include <algorithm>
#include <arpa/inet.h>
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <dlfcn.h>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <sys/socket.h>
#include <unistd.h>

namespace {
void exchange(int fd, void const* local, void* remote, std::size_t bytes) {
  auto* src = static_cast<char const*>(local);
  auto* dst = static_cast<char*>(remote);
  for (std::size_t n = 0; n < bytes;) {
    ssize_t got = send(fd, src + n, bytes - n, MSG_NOSIGNAL);
    if (got <= 0) throw std::runtime_error("bootstrap send failed");
    n += static_cast<std::size_t>(got);
  }
  for (std::size_t n = 0; n < bytes;) {
    ssize_t got = recv(fd, dst + n, bytes - n, 0);
    if (got <= 0) throw std::runtime_error("bootstrap recv failed or timed out");
    n += static_cast<std::size_t>(got);
  }
}

int connect_socket(char const* address, int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  if (fd < 0) throw std::runtime_error("socket failed");
  timeval timeout{30, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(port));
  if (std::strcmp(address, "listen") == 0) {
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) ||
        listen(fd, 1))
      throw std::runtime_error("bind/listen failed");
    int peer = accept(fd, nullptr, nullptr);
    close(fd);
    if (peer < 0) throw std::runtime_error("accept failed or timed out");
    fd = peer;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  } else if (inet_pton(AF_INET, address, &addr.sin_addr) != 1 ||
             connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr))) {
    throw std::runtime_error("connect failed (start listener first; use IPv4)");
  }
  return fd;
}
}  // namespace

int main(int argc, char** argv) {
  if (argc != 4) {
    std::cerr << "Usage: " << argv[0]
              << " listen|PEER_IPV4 PORT METAX_DEVICE\n";
    return 2;
  }
  try {
    int const port = std::stoi(argv[2]);
    int const device = std::stoi(argv[3]);
    if (port < 1 || port > 65535) throw std::runtime_error("invalid port");
    bool const listener = std::strcmp(argv[1], "listen") == 0;
    constexpr std::size_t bytes = 256 * 1024;
    char const* payload_env = std::getenv("UCCL_DIAG_PAYLOAD_BYTES");
    char const* iterations_env = std::getenv("UCCL_DIAG_ITERATIONS");
    std::size_t const payload_bytes =
        payload_env ? std::stoul(payload_env) : 64 * 1024;
    constexpr std::size_t destination_offset = 128 * 1024;
    unsigned long const iterations_value =
        iterations_env ? std::stoul(iterations_env) : 4096;
    if (payload_bytes == 0 || payload_bytes > 64 * 1024 ||
        payload_bytes % sizeof(uint32_t) != 0 || iterations_value == 0 ||
        iterations_value > 65536)
      throw std::runtime_error("invalid WRITE load parameters");
    int const iterations = static_cast<int>(iterations_value);
    constexpr int depth = 32;
    constexpr std::size_t atomic_bytes = 4096;
    bool const host_main = std::getenv("UCCL_DIAG_MAIN_HOST") &&
                           std::strcmp(std::getenv("UCCL_DIAG_MAIN_HOST"), "1") == 0;
    void* gpu = nullptr;
    void* host_base = nullptr;
    void* gpu_alias_base = nullptr;
    void* control = nullptr;
    uint64_t* gpu_control = nullptr;
    uccl::gpu_p2p::set_device(device);
    void* runtime = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_LOCAL);
    if (!runtime) throw std::runtime_error("cannot load MetaX runtime");
    using ExtMalloc = int (*)(void**, std::size_t, unsigned int);
    auto ext = reinterpret_cast<ExtMalloc>(dlsym(runtime, "mcExtMallocWithFlags"));
    if (host_main) {
      if (posix_memalign(&gpu, 4096, bytes) != 0 || !gpu)
        throw std::runtime_error("aligned host main allocation failed");
    } else if (!ext || ext(&gpu, bytes, 0x1) != 0 || !gpu) {
      throw std::runtime_error("fine-grained main allocation failed");
    }
    if (cudaHostAlloc(&host_base, atomic_bytes + 4095, cudaHostAllocMapped) != cudaSuccess)
      throw std::runtime_error("cudaHostAlloc failed");
    if (cudaHostGetDevicePointer(&gpu_alias_base, host_base, 0) != cudaSuccess)
      throw std::runtime_error("cudaHostGetDevicePointer failed");
    auto raw = reinterpret_cast<std::uintptr_t>(host_base);
    auto aligned = (raw + 4095) & ~static_cast<std::uintptr_t>(4095);
    auto offset = aligned - raw;
    control = reinterpret_cast<void*>(aligned);
    gpu_control = reinterpret_cast<uint64_t*>(static_cast<unsigned char*>(gpu_alias_base) + offset);
    std::memset(control, 0, atomic_bytes);
    uint64_t const initial[2] = {listener ? 11ULL : 21ULL, listener ? 17ULL : 27ULL};
    std::memcpy(control, initial, sizeof(initial));
    __atomic_thread_fence(__ATOMIC_RELEASE);
    if (host_main) std::memset(gpu, 0, bytes);
    else uccl::gpu_p2p::zero_memory(gpu, bytes, uccl::gpu_p2p::MemoryKind::Device, device);
    uint64_t gpu_before[2] = {};
    int read_rc = gpu_acquire_control(gpu_control, gpu_before);
    std::cout << "MAIN_KIND=" << (host_main ? "HOST" : "FINEGRAIN_GPU")
              << " ALLOCATION=HOST_MAPPED main=" << gpu << " control=" << control
              << " control_base=" << host_base << " gpu_control=" << gpu_control
              << " gpu_initial=" << gpu_before[0] << "," << gpu_before[1]
              << " gpu_read_rc=" << read_rc << std::endl;
    if (read_rc || gpu_before[0] != initial[0] || gpu_before[1] != initial[1])
      throw std::runtime_error("GPU cannot read initial mapped control");

    uint32_t const sent = listener ? 0x13579bdf : 0x2468ace0;
    uint32_t const expected = listener ? 0x2468ace0 : 0x13579bdf;
    std::vector<uint32_t> pattern(payload_bytes / sizeof(uint32_t), sent);
    if (host_main) std::memcpy(gpu, pattern.data(), payload_bytes);
    else uccl::gpu_p2p::copy_host_to_device(gpu, pattern.data(), payload_bytes, device);

    ProxyCtx ctx{};
    ctx.local_rank = device;
    UrmaTransport transport;
    transport.init(ctx);
    transport.register_main_buffer(gpu, bytes - 17, device);
    transport.register_atomic_buffer(control, atomic_bytes, device);
    {
      UrmaTransport duplicate;
      duplicate.init(ctx);
      duplicate.register_main_buffer(gpu, bytes - 17, device);
      duplicate.register_atomic_buffer(control, atomic_bytes, device);
      duplicate.destroy();
    }

    int fd = connect_socket(argv[1], port);
    auto local = transport.local_info();
    RemoteConnInfo remote{};
    exchange(fd, &local, &remote, sizeof(local));
    transport.connect_peer(0, remote);
    char ready = 1, peer_ready = 0;
    exchange(fd, &ready, &peer_ready, 1);
    auto const load_start = std::chrono::steady_clock::now();
    for (int start = 0; start < iterations; start += depth) {
      int const batch = std::min(depth, iterations - start);
      for (int i = 0; i < batch; ++i)
        transport.post_write(0, 1000 + start + i, 0, destination_offset,
                             payload_bytes, false);
      int done = 0;
      auto const batch_deadline =
          std::chrono::steady_clock::now() + std::chrono::seconds(30);
      while (done < batch) {
        TransportCompletion completions[depth];
        int const n = transport.poll(completions, depth);
        for (int i = 0; i < n; ++i)
          if (completions[i].status)
            throw std::runtime_error("bulk WRITE completion error");
        done += n;
        if (std::chrono::steady_clock::now() > batch_deadline)
          throw std::runtime_error("bulk WRITE completion timeout");
        std::this_thread::yield();
      }
    }
    auto const load_end = std::chrono::steady_clock::now();
    auto const elapsed_s =
        std::chrono::duration<double>(load_end - load_start).count();
    std::cout << "WRITE_LOAD iterations=" << iterations
              << " bytes_each=" << payload_bytes << " depth=" << depth
              << " payload_bytes=" << static_cast<uint64_t>(iterations) * payload_bytes
              << " elapsed_s=" << elapsed_s
              << " effective_GB_s=" << iterations * payload_bytes / elapsed_s / 1e9
              << std::endl;
    transport.post_write(0, 1, 0, destination_offset, payload_bytes, false);
    transport.post_atomic_add(0, 2, 0, 1, true);
    transport.post_atomic_add(0, 3, 8, -3, true);
    transport.post_barrier_atomic_add(0, 4, 0, 7);

    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    int complete = 0;
    while (complete < 4) {
      TransportCompletion completions[8];
      int const n = transport.poll(completions, 8);
      for (int i = 0; i < n; ++i) {
        std::cout << "CR wr_id=" << completions[i].wr_id
                  << " status=" << completions[i].status
                  << " is_atomic=" << completions[i].is_atomic << std::endl;
        if (completions[i].status)
          throw std::runtime_error("URMA completion error");
      }
      complete += n;
      if (std::chrono::steady_clock::now() > deadline)
        throw std::runtime_error("URMA completion timeout");
      std::this_thread::yield();
    }

    exchange(fd, &ready, &peer_ready, 1);
    std::vector<uint32_t> received(payload_bytes / sizeof(uint32_t));
    uint64_t atomic_value[2] = {};
    uint64_t gpu_value[2] = {};
    if (host_main)
      std::memcpy(received.data(), static_cast<unsigned char*>(gpu) + destination_offset, payload_bytes);
    else
      uccl::gpu_p2p::copy_device_to_host(
          received.data(), static_cast<unsigned char*>(gpu) + destination_offset,
          payload_bytes, device);
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    std::memcpy(atomic_value, control, sizeof(atomic_value));
    int gpu_after_rc = gpu_acquire_control(gpu_control, gpu_value);
    uint64_t host_barrier = transport.load_barrier_word(0);
    transport.destroy();
    close(fd);
    if (cudaFreeHost(host_base) != cudaSuccess)
      throw std::runtime_error("cudaFreeHost failed");
    if (host_main) std::free(gpu);
    else uccl::gpu_p2p::release_memory(gpu, uccl::gpu_p2p::MemoryKind::Device, device);
    std::cout << "HOST_FADD cpu=" << atomic_value[0] << "," << atomic_value[1]
              << " gpu_acquire=" << gpu_value[0] << "," << gpu_value[1]
              << " gpu_read_rc=" << gpu_after_rc << std::endl;
    if (gpu_after_rc || atomic_value[0] != initial[0] + 1 ||
        atomic_value[1] != initial[1] - 3 || gpu_value[0] != atomic_value[0] ||
        gpu_value[1] != atomic_value[1])
      throw std::runtime_error("mapped host FADD or GPU acquire mismatch");
    for (auto value : received)
      if (value != expected)
        throw std::runtime_error("MetaX P2P WRITE payload mismatch");
    std::cout << "WRITE_DATA_MATCH=1" << std::endl;
    std::cout << "FADD_RESULT gpu_positive=" << atomic_value[0]
              << " gpu_negative_bits=" << atomic_value[1]
              << " host_barrier=" << host_barrier << std::endl;
    if (host_barrier != 7)
      throw std::runtime_error("URMA host barrier FADD result mismatch");
    std::cout << "MAPPED_HOST_GPU_FADD_PASS" << std::endl;
    std::cout << "URMA MetaX smoke passed: duplicate registration, "
                 "bidirectional WRITE + positive/negative FADD\n";
  } catch (std::exception const& e) {
    std::cerr << "URMA MetaX smoke failed: " << e.what() << '\n';
    return 1;
  }
}
