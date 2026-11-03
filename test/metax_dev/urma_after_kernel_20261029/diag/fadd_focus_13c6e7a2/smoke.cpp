// Two-node MetaX production-transport smoke. No perftest or mock provider.
#include "gpu_p2p_runtime.hpp"
#include "urma_transport.hpp"

#include <arpa/inet.h>
#include <chrono>
#include <cstring>
#include <cstdlib>
#include <cstdint>
#include <dlfcn.h>
#include <iostream>
#include <stdexcept>
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
    bool const read_target = std::getenv("UCCL_DIAG_READ_TARGET") != nullptr;
    std::cout << "TARGET_MODE=" << (read_target ? "READ" : "FADD") << std::endl;
    constexpr std::size_t bytes = 64 * 1024;
    constexpr std::size_t atomic_bytes = 4096;
    bool const fine = std::getenv("UCCL_DIAG_FINEGRAIN") &&
                      std::strcmp(std::getenv("UCCL_DIAG_FINEGRAIN"), "1") == 0;
    void* gpu = nullptr;
    void* control = nullptr;
    void* control_base = nullptr;
    if (fine) {
      uccl::gpu_p2p::set_device(device);
      void* runtime = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_LOCAL);
      if (!runtime) throw std::runtime_error("cannot load MetaX runtime for fine allocation");
      using ExtMalloc = int (*)(void**, std::size_t, unsigned int);
      auto ext = reinterpret_cast<ExtMalloc>(dlsym(runtime, "mcExtMallocWithFlags"));
      if (!ext) throw std::runtime_error("mcExtMallocWithFlags not found");
      constexpr unsigned int fine_grained = 0x1; // mcDeviceMallocFinegrained in this SDK
      if (ext(&gpu, bytes, fine_grained) != 0 || !gpu)
        throw std::runtime_error("fine-grained main allocation failed");
      constexpr std::size_t alignment = 8192;
      if (ext(&control_base, atomic_bytes + 2 * alignment, fine_grained) != 0 || !control_base)
        throw std::runtime_error("fine-grained control allocation failed");
      auto raw = reinterpret_cast<std::uintptr_t>(control_base);
      control = reinterpret_cast<void*>((raw + alignment) & ~(alignment - 1));
    } else {
      gpu = uccl::gpu_p2p::allocate_device(bytes, device);
      control = uccl::gpu_p2p::allocate_atomic_buffer(atomic_bytes, device);
      control_base = control;
    }
    std::cout << "ALLOCATION=" << (fine ? "finegrained" : "mcMalloc")
              << " main=" << gpu << " control=" << control
              << " control_base=" << control_base << std::endl;
    uccl::gpu_p2p::zero_memory(gpu, bytes,
                               uccl::gpu_p2p::MemoryKind::Device, device);
    uccl::gpu_p2p::zero_memory(control, atomic_bytes,
                               uccl::gpu_p2p::MemoryKind::Device, device);

    uint64_t const initial[2] = {listener ? 11ULL : 21ULL, listener ? 17ULL : 27ULL};
    uccl::gpu_p2p::copy_host_to_device(control, initial, sizeof(initial), device);
    uint64_t initial_readback[2] = {};
    uccl::gpu_p2p::copy_device_to_host(initial_readback, control, sizeof(initial_readback), device);
    std::cout << "INITIAL_GPU=" << initial_readback[0] << "," << initial_readback[1] << std::endl;
    if (initial_readback[0] != initial[0] || initial_readback[1] != initial[1])
      throw std::runtime_error("initial GPU readback mismatch");
    auto main_owner = UrmaTransport::acquire_buffer_ownership(gpu, false, device);
    auto control_owner = UrmaTransport::acquire_buffer_ownership(control, false, device, control_base);
    uint32_t const sent = listener ? 0x13579bdf : 0x2468ace0;
    uint32_t const expected = listener ? 0x2468ace0 : 0x13579bdf;
    std::vector<uint32_t> pattern(1024, sent);
    uccl::gpu_p2p::copy_host_to_device(gpu, pattern.data(), 4096, device);
    uccl::gpu_p2p::copy_host_to_device(
        static_cast<unsigned char*>(gpu) + 4096, initial, sizeof(initial), device);

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
    if (setenv("UCCL_DIAG_WRITE_ATOMIC_TARGET", "1", 1))
      throw std::runtime_error("cannot select diagnostic target");
    transport.post_write(0, 11, 4096, 0, sizeof(initial), false);
    if (unsetenv("UCCL_DIAG_WRITE_ATOMIC_TARGET"))
      throw std::runtime_error("cannot reset diagnostic target");
    auto write_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    bool write_done = false;
    while (!write_done) {
      TransportCompletion completion[1];
      int const n = transport.poll(completion, 1);
      if (n) {
        if (completion[0].wr_id != 11 || completion[0].status)
          throw std::runtime_error("atomic-segment WRITE completion mismatch");
        write_done = true;
      }
      if (std::chrono::steady_clock::now() > write_deadline)
        throw std::runtime_error("atomic-segment WRITE completion timeout");
      std::this_thread::yield();
    }
    exchange(fd, &ready, &peer_ready, 1);
    uint64_t written[2] = {};
    uint64_t const expected_written[2] = {listener ? 21ULL : 11ULL,
                                          listener ? 27ULL : 17ULL};
    uccl::gpu_p2p::copy_device_to_host(written, control, sizeof(written), device);
    std::cout << "ATOMIC_SEG_WRITE_RESULT=" << written[0] << "," << written[1]
              << " expected=" << expected_written[0] << "," << expected_written[1]
              << std::endl;
    char local_write_ok = written[0] == expected_written[0] &&
                          written[1] == expected_written[1];
    char peer_write_ok = 0;
    exchange(fd, &local_write_ok, &peer_write_ok, 1);
    if (!local_write_ok || !peer_write_ok) {
      std::cout << "ATOMIC_SEG_WRITE_FAILED local=" << static_cast<int>(local_write_ok)
                << " peer=" << static_cast<int>(peer_write_ok) << std::endl;
      main_owner.reset();
      control_owner.reset();
      transport.destroy();
      std::cout << "TRANSPORT_DESTROY_OK" << std::endl;
      close(fd);
      return 2;
    }
    std::cout << "ATOMIC_SEG_WRITE_VERIFIED" << std::endl;
    transport.post_write(0, 1, 0, 16384, 4096, false);
    transport.post_atomic_add(0, 2, 0, 1, true);
    transport.post_atomic_add(0, 3, 8, -3, true);
    transport.post_barrier_atomic_add(0, 4, 0, 7);

    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    int complete = 0;
    uint64_t fetched[2] = {};
    while (complete < 4) {
      TransportCompletion completions[8];
      int const n = transport.poll(completions, 8);
      for (int i = 0; i < n; ++i) {
        std::cout << "CR wr_id=" << completions[i].wr_id
                  << " status=" << completions[i].status
                  << " is_atomic=" << completions[i].is_atomic << std::endl;
        if (completions[i].status)
          throw std::runtime_error("URMA completion error");
        if (completions[i].wr_id == 2 || completions[i].wr_id == 3)
          fetched[completions[i].wr_id - 2] = completions[i].data;
      }
      complete += n;
      if (std::chrono::steady_clock::now() > deadline)
        throw std::runtime_error("URMA completion timeout");
      std::this_thread::yield();
    }

    exchange(fd, &ready, &peer_ready, 1);
    std::vector<uint32_t> received(1024);
    uint64_t atomic_value[2] = {};
    uccl::gpu_p2p::copy_device_to_host(
        received.data(), static_cast<unsigned char*>(gpu) + 16384, 4096,
        device);
    uccl::gpu_p2p::copy_device_to_host(&atomic_value, control,
                                       sizeof(atomic_value), device);
    uint64_t host_barrier = transport.load_barrier_word(0);
    main_owner.reset();
    control_owner.reset();
    std::cout << "OWNER_LEASES_RELEASED_WHILE_REGISTERED" << std::endl;
    transport.destroy();
    std::cout << "TRANSPORT_DESTROY_OK" << std::endl;
    close(fd);
    for (auto value : received)
      if (value != expected)
        throw std::runtime_error("MetaX P2P WRITE payload mismatch");
    std::cout << "WRITE_DATA_MATCH=1" << std::endl;
    std::cout << "FADD_RESULT gpu_positive=" << atomic_value[0]
              << " gpu_negative_bits=" << atomic_value[1]
              << " host_barrier=" << host_barrier << std::endl;
    if (host_barrier != 7)
      throw std::runtime_error("URMA host barrier FADD result mismatch");
    std::cout << "FETCHED=" << fetched[0] << "," << fetched[1]
              << " EXPECTED=" << initial[0] << "," << initial[1] << std::endl;
    if (fetched[0] != initial[0] || fetched[1] != initial[1])
      throw std::runtime_error("GPU target returned-value mismatch");
    if (read_target) {
      if (atomic_value[0] != expected_written[0] || atomic_value[1] != expected_written[1])
        throw std::runtime_error("READ unexpectedly changed GPU target");
      std::cout << "SAME_GPU_TARGET_READ_PASS" << std::endl;
      return 0;
    }
    if (atomic_value[0] != expected_written[0] + 1 ||
        atomic_value[1] != expected_written[1] - 3)
      throw std::runtime_error("MetaX P2P FADD result mismatch");
    std::cout << "URMA MetaX smoke passed: duplicate registration, "
                 "bidirectional WRITE + positive/negative FADD\n";
  } catch (std::exception const& e) {
    std::cerr << "URMA MetaX smoke failed: " << e.what() << '\n';
    return 1;
  }
}
