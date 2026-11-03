// Two-node, bidirectional GPU WRITE probe matching the URMA write_load shape.
// Payload buffers are MetaX fine-grained allocations registered with DMA-BUF.
#include <infiniband/verbs.h>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#include <dlfcn.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {
std::size_t kBufferBytes = 0;
std::size_t kRegistrationBytes = 0;
std::size_t kPayload = 0;
std::size_t kRemoteOffset = 0;
constexpr int kDepth = 32;

void require(bool ok, std::string const& message) {
  if (!ok) throw std::runtime_error(message);
}

template <typename T> T symbol(void* lib, char const* name) {
  auto fn = reinterpret_cast<T>(dlsym(lib, name));
  require(fn != nullptr, std::string("Missing symbol: ") + name);
  return fn;
}

struct Resources {
  void* runtime = nullptr;
  void* driver = nullptr;
  void* gpu = nullptr;
  int (*gpu_free)(void*) = nullptr;
  ibv_context* ctx = nullptr;
  ibv_pd* pd = nullptr;
  ibv_cq* cq = nullptr;
  ibv_qp* qp = nullptr;
  ibv_mr* mr = nullptr;
  int fd = -1;
  ~Resources() {
    if (fd >= 0) close(fd);
    if (qp && ibv_destroy_qp(qp)) std::cerr << "CLEANUP_ERROR destroy_qp\n";
    if (mr && ibv_dereg_mr(mr)) std::cerr << "CLEANUP_ERROR dereg_mr\n";
    if (cq && ibv_destroy_cq(cq)) std::cerr << "CLEANUP_ERROR destroy_cq\n";
    if (pd && ibv_dealloc_pd(pd)) std::cerr << "CLEANUP_ERROR dealloc_pd\n";
    if (ctx && ibv_close_device(ctx)) std::cerr << "CLEANUP_ERROR close_device\n";
    if (gpu && gpu_free && gpu_free(gpu)) std::cerr << "CLEANUP_ERROR mcFree\n";
    if (driver) dlclose(driver);
    if (runtime) dlclose(runtime);
  }
};

void exchange(int fd, void const* local, void* remote, std::size_t n) {
  auto* src = static_cast<char const*>(local);
  auto* dst = static_cast<char*>(remote);
  for (std::size_t i = 0; i < n;) {
    auto count = send(fd, src + i, n - i, MSG_NOSIGNAL);
    require(count > 0, "bootstrap send failed");
    i += static_cast<std::size_t>(count);
  }
  for (std::size_t i = 0; i < n;) {
    auto count = recv(fd, dst + i, n - i, 0);
    require(count > 0, "bootstrap receive failed");
    i += static_cast<std::size_t>(count);
  }
}

int bootstrap(char const* address, int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  require(fd >= 0, "socket failed");
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
    require(bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0 &&
                listen(fd, 1) == 0, "bind/listen failed");
    int peer = accept(fd, nullptr, nullptr);
    close(fd);
    require(peer >= 0, "accept failed or timed out");
    fd = peer;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
  } else {
    require(inet_pton(AF_INET, address, &addr.sin_addr) == 1 &&
                connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0,
            "connect failed");
  }
  return fd;
}

struct Connection {
  uint64_t address;
  uint32_t rkey;
  uint32_t qpn;
  uint8_t gid[16];
};

int run(int argc, char** argv) {
  require(argc == 5, "Usage: verbs_write listen|IP PORT GPU ITERATIONS");
  int port = std::stoi(argv[2]), device = std::stoi(argv[3]);
  int iterations = std::stoi(argv[4]);
  require(port > 0 && port < 65536 && device >= 0 && iterations > 0 &&
              iterations <= 65536, "Invalid argument");
  char const* payload_env = std::getenv("UCCL_DIAG_PAYLOAD_BYTES");
  kPayload = payload_env ? std::stoul(payload_env) : 65536;
  require(kPayload >= 4096 && kPayload <= 4194304 && kPayload % 4096 == 0, "Invalid payload");
  kBufferBytes = 4 * kPayload;
  kRegistrationBytes = kBufferBytes - 17;
  kRemoteOffset = 2 * kPayload;
  bool listener = std::strcmp(argv[1], "listen") == 0;
  Resources r;
  r.runtime = dlopen("/opt/maca-3.7.0/lib/libmcruntime.so", RTLD_NOW | RTLD_LOCAL);
  require(r.runtime != nullptr, "Cannot load MetaX runtime");
  auto set_device = symbol<int (*)(int)>(r.runtime, "mcSetDevice");
  auto alloc = symbol<int (*)(void**, std::size_t, unsigned int)>(r.runtime, "mcExtMallocWithFlags");
  auto copy = symbol<int (*)(void*, void const*, std::size_t, int)>(r.runtime, "mcMemcpy");
  r.gpu_free = symbol<int (*)(void*)>(r.runtime, "mcFree");
  require(set_device(device) == 0, "mcSetDevice failed");
  require(alloc(&r.gpu, kBufferBytes, 0x1) == 0 && r.gpu, "fine-grained GPU allocation failed");

  uint32_t const sent = listener ? 0x13579bdf : 0x2468ace0;
  uint32_t const expected = listener ? 0x2468ace0 : 0x13579bdf;
  std::vector<uint32_t> initial(kBufferBytes / sizeof(uint32_t), 0);
  std::fill(initial.begin(), initial.begin() + kPayload / sizeof(uint32_t), sent);
  require(copy(r.gpu, initial.data(), kBufferBytes, 1) == 0, "GPU H2D initialization failed");

  int ndev = 0;
  ibv_device** devices = ibv_get_device_list(&ndev);
  require(devices != nullptr, "ibv_get_device_list failed");
  for (int i = 0; i < ndev; ++i)
    if (std::strcmp(ibv_get_device_name(devices[i]), "rocep40s0f0") == 0) {
      r.ctx = ibv_open_device(devices[i]); break;
    }
  ibv_free_device_list(devices);
  require(r.ctx != nullptr, "Cannot open rocep40s0f0");
  ibv_port_attr port_attr{};
  require(ibv_query_port(r.ctx, 1, &port_attr) == 0 &&
              port_attr.state == IBV_PORT_ACTIVE &&
              port_attr.link_layer == IBV_LINK_LAYER_ETHERNET,
          "RoCE port unavailable");
  ibv_gid gid{};
  require(ibv_query_gid(r.ctx, 1, 3, &gid) == 0, "GID index 3 unavailable");
  r.pd = ibv_alloc_pd(r.ctx);
  require(r.pd != nullptr, "ibv_alloc_pd failed");

  r.driver = dlopen("libmxc-runtime64.so.1", RTLD_NOW | RTLD_LOCAL);
  require(r.driver != nullptr, "Cannot load MetaX DMA-BUF exporter");
  auto mxc_init = symbol<int (*)()>(r.driver, "mxc_init");
  auto export_dmabuf = symbol<int (*)(void*, std::size_t, int*, uint64_t*)>(
      r.driver, "mxc_vendor_portable_export_dmabuf");
  require(mxc_init() == 0, "mxc_init failed");
  int dmabuf_fd = -1;
  uint64_t offset = 0;
  require(export_dmabuf(r.gpu, kRegistrationBytes, &dmabuf_fd, &offset) == 0 && dmabuf_fd >= 0,
          "GPU DMA-BUF export failed");
  r.mr = ibv_reg_dmabuf_mr(r.pd, offset, kRegistrationBytes,
                           reinterpret_cast<uint64_t>(r.gpu), dmabuf_fd,
                           IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE |
                               IBV_ACCESS_REMOTE_READ);
  int saved_errno = errno;
  close(dmabuf_fd);
  require(r.mr != nullptr, "ibv_reg_dmabuf_mr failed errno=" + std::to_string(saved_errno));
  r.cq = ibv_create_cq(r.ctx, 128, nullptr, nullptr, 0);
  require(r.cq != nullptr, "ibv_create_cq failed");
  ibv_qp_init_attr qpi{};
  qpi.send_cq = r.cq; qpi.recv_cq = r.cq; qpi.qp_type = IBV_QPT_RC;
  qpi.cap.max_send_wr = 64; qpi.cap.max_recv_wr = 1;
  qpi.cap.max_send_sge = 1; qpi.cap.max_recv_sge = 1;
  r.qp = ibv_create_qp(r.pd, &qpi);
  require(r.qp != nullptr, "ibv_create_qp failed");
  ibv_qp_attr init{};
  init.qp_state = IBV_QPS_INIT; init.port_num = 1; init.pkey_index = 0;
  init.qp_access_flags = IBV_ACCESS_LOCAL_WRITE | IBV_ACCESS_REMOTE_WRITE |
                         IBV_ACCESS_REMOTE_READ;
  require(ibv_modify_qp(r.qp, &init, IBV_QP_STATE | IBV_QP_PKEY_INDEX |
                       IBV_QP_PORT | IBV_QP_ACCESS_FLAGS) == 0, "QP INIT failed");
  std::cout << "GPU_KIND=FINEGRAIN_GPU GPU_REG=DMA_BUF allocation=" << kBufferBytes
            << " registered=" << kRegistrationBytes
            << " HCA=rocep40s0f0 GID_INDEX=3 depth=" << kDepth << std::endl;

  r.fd = bootstrap(argv[1], port);
  Connection local{}, peer{};
  local.address = reinterpret_cast<uint64_t>(r.gpu);
  local.rkey = r.mr->rkey; local.qpn = r.qp->qp_num;
  std::memcpy(local.gid, &gid, 16);
  exchange(r.fd, &local, &peer, sizeof(local));
  require(peer.address != 0 && peer.qpn != 0 && peer.rkey != 0,
          "Invalid remote connection");

  ibv_qp_attr rtr{};
  rtr.qp_state = IBV_QPS_RTR; rtr.path_mtu = port_attr.active_mtu;
  rtr.dest_qp_num = peer.qpn; rtr.rq_psn = 0; rtr.max_dest_rd_atomic = 1;
  rtr.min_rnr_timer = 12; rtr.ah_attr.is_global = 1; rtr.ah_attr.port_num = 1;
  rtr.ah_attr.sl = 3; rtr.ah_attr.grh.sgid_index = 3;
  rtr.ah_attr.grh.hop_limit = 255; rtr.ah_attr.grh.traffic_class = 104;
  std::memcpy(&rtr.ah_attr.grh.dgid, peer.gid, 16);
  require(ibv_modify_qp(r.qp, &rtr, IBV_QP_STATE | IBV_QP_PATH_MTU | IBV_QP_AV |
                        IBV_QP_DEST_QPN | IBV_QP_RQ_PSN | IBV_QP_MAX_DEST_RD_ATOMIC |
                        IBV_QP_MIN_RNR_TIMER) == 0, "QP RTR failed errno=" + std::to_string(errno));
  ibv_qp_attr rts{};
  rts.qp_state = IBV_QPS_RTS; rts.timeout = 14; rts.retry_cnt = 7;
  rts.rnr_retry = 7; rts.sq_psn = 0; rts.max_rd_atomic = 1;
  require(ibv_modify_qp(r.qp, &rts, IBV_QP_STATE | IBV_QP_TIMEOUT | IBV_QP_RETRY_CNT |
                        IBV_QP_RNR_RETRY | IBV_QP_SQ_PSN | IBV_QP_MAX_QP_RD_ATOMIC) == 0,
          "QP RTS failed errno=" + std::to_string(errno));
  char ready = 1, peer_ready = 0;
  exchange(r.fd, &ready, &peer_ready, 1);
  require(peer_ready == 1, "peer not ready");
  auto start = std::chrono::steady_clock::now();
  for (int base = 0; base < iterations; base += kDepth) {
    int batch = std::min(kDepth, iterations - base);
    for (int j = 0; j < batch; ++j) {
      ibv_sge sge{};
      sge.addr = reinterpret_cast<uint64_t>(r.gpu); sge.length = kPayload;
      sge.lkey = r.mr->lkey;
      ibv_send_wr wr{}, *bad = nullptr;
      wr.wr_id = static_cast<uint64_t>(1000 + base + j);
      wr.sg_list = &sge; wr.num_sge = 1; wr.opcode = IBV_WR_RDMA_WRITE;
      wr.send_flags = IBV_SEND_SIGNALED;
      wr.wr.rdma.remote_addr = peer.address + kRemoteOffset;
      wr.wr.rdma.rkey = peer.rkey;
      require(ibv_post_send(r.qp, &wr, &bad) == 0, "ibv_post_send failed");
    }
    int done = 0;
    std::vector<bool> seen(static_cast<std::size_t>(batch), false);
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(30);
    while (done < batch) {
      ibv_wc wc[kDepth]{};
      int n = ibv_poll_cq(r.cq, kDepth, wc);
      require(n >= 0, "ibv_poll_cq failed");
      for (int j = 0; j < n; ++j) {
        require(wc[j].status == IBV_WC_SUCCESS && wc[j].opcode == IBV_WC_RDMA_WRITE &&
                    wc[j].wr_id >= static_cast<uint64_t>(1000 + base) &&
                    wc[j].wr_id < static_cast<uint64_t>(1000 + base + batch),
                "RDMA WRITE completion failed status=" + std::to_string(wc[j].status));
        auto index = static_cast<std::size_t>(wc[j].wr_id - (1000 + base));
        require(!seen[index], "duplicate RDMA WRITE completion");
        seen[index] = true;
      }
      done += n;
      require(done <= batch && std::chrono::steady_clock::now() < deadline,
              "RDMA WRITE completion timeout");
      std::this_thread::yield();
    }
  }
  double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
  exchange(r.fd, &ready, &peer_ready, 1);
  std::vector<uint32_t> received(kPayload / sizeof(uint32_t));
  require(copy(received.data(), static_cast<unsigned char*>(r.gpu) + kRemoteOffset,
               kPayload, 2) == 0, "GPU D2H verification failed");
  int valid = std::all_of(received.begin(), received.end(),
                          [expected](uint32_t value) { return value == expected; });
  int peer_valid = 0;
  exchange(r.fd, &valid, &peer_valid, sizeof(valid));
  require(valid && peer_valid, "GPU WRITE data mismatch");
  std::cout << "WRITE_LOAD iterations=" << iterations << " bytes_each=" << kPayload
            << " depth=" << kDepth << " payload_bytes=" << uint64_t(iterations) * kPayload
            << " elapsed_s=" << elapsed << " effective_GB_s="
            << iterations * kPayload / elapsed / 1e9 << std::endl;
  std::cout << "WRITE_DATA_MATCH=1 PEER_MATCH=1\n";
  return 0;
}
}  // namespace

int main(int argc, char** argv) {
  try { return run(argc, argv); }
  catch (std::exception const& e) { std::cerr << "PROBE_ERROR " << e.what() << '\n'; return 1; }
}
