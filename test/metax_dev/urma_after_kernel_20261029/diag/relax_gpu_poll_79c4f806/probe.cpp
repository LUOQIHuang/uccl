// Stress RELAX WRITE -> STRONG FADD with a live GPU acquire poll.
#include "gpu_p2p_runtime.hpp"
#include "urma_transport.hpp"
#include <cuda_runtime.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <dlfcn.h>

#include <chrono>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

extern "C" int relax_poll_start(uint64_t*, uint32_t const*, uint64_t*, uint32_t, int);
extern "C" int relax_poll_finish();

namespace {
constexpr int kRounds = 256;
constexpr int kChunks = 8;
constexpr std::size_t kChunkBytes = 512;
constexpr std::size_t kPayloadBytes = kChunks * kChunkBytes;
constexpr std::size_t kMainBytes = (kRounds + 1) * kPayloadBytes;
constexpr std::size_t kDestOffset = kRounds * kPayloadBytes;
constexpr std::size_t kAtomicBytes = 8192;

void require(bool condition, std::string const& message) {
  if (!condition) throw std::runtime_error(message);
}
void exchange(int fd, void const* local, void* remote, std::size_t bytes) {
  auto const* src = static_cast<char const*>(local);
  auto* dst = static_cast<char*>(remote);
  for (std::size_t n = 0; n < bytes;) {
    ssize_t done = send(fd, src + n, bytes - n, MSG_NOSIGNAL);
    require(done > 0, "bootstrap send failed"); n += static_cast<std::size_t>(done);
  }
  for (std::size_t n = 0; n < bytes;) {
    ssize_t done = recv(fd, dst + n, bytes - n, 0);
    require(done > 0, "bootstrap recv failed"); n += static_cast<std::size_t>(done);
  }
}
int connect_socket(char const* host, int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  require(fd >= 0, "socket failed");
  timeval timeout{30, 0};
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  sockaddr_in addr{}; addr.sin_family = AF_INET; addr.sin_port = htons(port);
  if (std::strcmp(host, "listen") == 0) {
    int one = 1; setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    require(bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0 &&
            listen(fd, 1) == 0, "bind/listen failed");
    int peer = accept(fd, nullptr, nullptr); close(fd);
    require(peer >= 0, "accept failed"); fd = peer;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  } else {
    require(inet_pton(AF_INET, host, &addr.sin_addr) == 1 &&
            connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0,
            "connect failed");
  }
  return fd;
}

struct Resources {
  ProxyCtx ctx{};
  UrmaTransport transport;
  void* runtime = nullptr;
  void* main = nullptr;
  void* atomic_base = nullptr;
  uint64_t* status = nullptr;
  uint64_t* gpu_status = nullptr;
  int (*gpu_free)(void*) = nullptr;
  int fd = -1;
  bool poll_started = false;
  bool cleaned = false;
  bool cleanup() {
    if (cleaned) return true;
    cleaned = true;
    bool ok = true;
    if (poll_started) {
      __atomic_store_n(status + 7, uint64_t{1}, __ATOMIC_RELEASE);
      if (relax_poll_finish() != 0) { std::cerr << "CLEANUP_ERROR GPU poll\n"; ok = false; }
      poll_started = false;
    }
    try { transport.destroy(); }
    catch (...) { std::cerr << "CLEANUP_ERROR transport\n"; ok = false; }
    if (fd >= 0 && close(fd) != 0) { std::cerr << "CLEANUP_ERROR socket\n"; ok = false; }
    if (status && cudaFreeHost(status) != cudaSuccess) { std::cerr << "CLEANUP_ERROR host status\n"; ok = false; }
    if (atomic_base && gpu_free(atomic_base)) { std::cerr << "CLEANUP_ERROR atomic\n"; ok = false; }
    if (main && gpu_free(main)) { std::cerr << "CLEANUP_ERROR main\n"; ok = false; }
    if (runtime) dlclose(runtime);
    return ok;
  }
  ~Resources() { cleanup(); }
};

struct Ack { uint64_t sequence; uint64_t errors; };
int run(int argc, char** argv) {
  require(argc == 4, "Usage: relax_order_probe listen|IP PORT DEVICE");
  int const port = std::stoi(argv[2]), device = std::stoi(argv[3]);
  require(port > 0 && port < 65536 && device >= 0, "bad argument");
  bool const listener = std::strcmp(argv[1], "listen") == 0;
  uint32_t const send_base = listener ? 0x13579bdf : 0x2468ace0;
  uint32_t const expect_base = listener ? 0x2468ace0 : 0x13579bdf;
  Resources r;
  uccl::gpu_p2p::set_device(device);
  r.runtime = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_LOCAL);
  require(r.runtime != nullptr, "runtime unavailable");
  auto alloc = reinterpret_cast<int (*)(void**, std::size_t, unsigned int)>(
      dlsym(r.runtime, "mcExtMallocWithFlags"));
  r.gpu_free = reinterpret_cast<int (*)(void*)>(dlsym(r.runtime, "mcFree"));
  require(alloc && r.gpu_free, "runtime symbols unavailable");
  require(alloc(&r.main, kMainBytes, 0x1) == 0 && r.main, "main GPU allocation failed");
  require(alloc(&r.atomic_base, kAtomicBytes + 8191, 0x1) == 0 && r.atomic_base,
          "atomic GPU allocation failed");
  auto raw = reinterpret_cast<uintptr_t>(r.atomic_base);
  require(raw <= UINTPTR_MAX - 8191, "atomic alignment overflow");
  auto* signal = reinterpret_cast<uint64_t*>((raw + 8191) & ~uintptr_t{8191});
  require(cudaHostAlloc(reinterpret_cast<void**>(&r.status), 8 * sizeof(uint64_t),
                        cudaHostAllocMapped) == cudaSuccess && r.status,
          "mapped status allocation failed");
  require(cudaHostGetDevicePointer(reinterpret_cast<void**>(&r.gpu_status), r.status, 0) == cudaSuccess,
          "GPU status alias failed");
  std::memset(r.status, 0, 8 * sizeof(uint64_t));
  std::vector<uint32_t> initial(kMainBytes / sizeof(uint32_t), 0);
  for (int sequence = 1; sequence <= kRounds; ++sequence)
    for (std::size_t word = 0; word < kPayloadBytes / sizeof(uint32_t); ++word)
      initial[(sequence - 1) * kPayloadBytes / sizeof(uint32_t) + word] = send_base ^ sequence;
  uccl::gpu_p2p::copy_host_to_device(r.main, initial.data(), kMainBytes, device);
  uccl::gpu_p2p::zero_memory(signal, kAtomicBytes, uccl::gpu_p2p::MemoryKind::Device, device);
  r.ctx.local_rank = device;
  r.transport.init(r.ctx);
  r.transport.register_main_buffer(r.main, kMainBytes, device);
  r.transport.register_atomic_buffer(signal, kAtomicBytes, device);
  r.fd = connect_socket(argv[1], port);
  auto local = r.transport.local_info(); RemoteConnInfo remote{};
  exchange(r.fd, &local, &remote, sizeof(local));
  r.transport.connect_peer(0, remote);
  auto* target = reinterpret_cast<uint32_t const*>(static_cast<unsigned char*>(r.main) + kDestOffset);
  int start_rc = relax_poll_start(signal, target, r.gpu_status, expect_base, kRounds);
  r.poll_started = true;
  require(start_rc == 0, "GPU poll launch failed");
  auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
  while (__atomic_load_n(r.status, __ATOMIC_ACQUIRE) != 1) {
    require(std::chrono::steady_clock::now() < deadline, "GPU poll did not start");
    std::this_thread::yield();
  }
  char ready = 1, peer_ready = 0; exchange(r.fd, &ready, &peer_ready, 1);
  for (int sequence = 1; sequence <= kRounds; ++sequence) {
    uint64_t const wr_base = static_cast<uint64_t>(sequence) * 16;
    for (int chunk = 0; chunk < kChunks; ++chunk)
      r.transport.post_write(0, wr_base + chunk,
                             (sequence - 1) * kPayloadBytes + chunk * kChunkBytes,
                             kDestOffset + chunk * kChunkBytes, kChunkBytes, false);
    r.transport.post_atomic_add(0, wr_base + kChunks, 0, 1, true);
    deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
    while (__atomic_load_n(r.status + 1, __ATOMIC_ACQUIRE) < static_cast<uint64_t>(sequence)) {
      require(std::chrono::steady_clock::now() < deadline, "GPU notification timeout");
      std::this_thread::yield();
    }
    Ack local_ack{static_cast<uint64_t>(sequence), r.status[2] + r.status[3]}, peer_ack{};
    exchange(r.fd, &local_ack, &peer_ack, sizeof(local_ack));
    bool seen[kChunks + 1]{};
    int completed = 0;
    deadline = std::chrono::steady_clock::now() + std::chrono::seconds(15);
    while (completed < kChunks + 1) {
      TransportCompletion events[kChunks + 1];
      int const n = r.transport.poll(events, kChunks + 1);
      for (int i = 0; i < n; ++i) {
        require(events[i].status == 0 && events[i].wr_id >= wr_base &&
                events[i].wr_id <= wr_base + kChunks, "completion status/id mismatch");
        auto index = static_cast<int>(events[i].wr_id - wr_base);
        require(!seen[index] && events[i].is_atomic == (index == kChunks), "duplicate/wrong completion");
        seen[index] = true;
      }
      completed += n;
      require(completed <= kChunks + 1 && std::chrono::steady_clock::now() < deadline,
              "completion timeout");
      std::this_thread::yield();
    }
    require(local_ack.errors == 0 && peer_ack.errors == 0 &&
            peer_ack.sequence == static_cast<uint64_t>(sequence),
            "GPU observed stale data or incorrect notification sequence");
    if (sequence % 64 == 0) std::cout << "VERIFIED_ROUNDS=" << sequence << std::endl;
  }
  uint64_t const mismatches = r.status[2], first_bad = r.status[4];
  require(r.cleanup(), "cleanup failed");
  std::cout << "RELAX_WRITE_STRONG_GPU_FADD_POLL_PASS rounds=" << kRounds
            << " writes_per_round=" << kChunks << " mismatches=" << mismatches
            << " first_bad_round=" << first_bad << std::endl;
  return 0;
}
}  // namespace

int main(int argc, char** argv) {
  try { return run(argc, argv); }
  catch (std::exception const& error) {
    std::cerr << "ORDER_PROBE_ERROR " << error.what() << std::endl; return 1;
  }
}
