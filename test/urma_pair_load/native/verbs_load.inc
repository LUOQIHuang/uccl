void VerbsWriteSession::load_prepare(size_t limit, int rank, size_t count) {
  auto& p = *impl_;
  size_t lanes = p.serial_lanes;
  ok(p.serial && lanes == 3 && uccl::serial_pair::valid_load_shape(lanes, limit, count),
     "invalid verbs depth configuration");
  prepare(459264, 0, 0, rank);
  p.g.window = count / lanes;
  p.g.slots = count;
  p.load_limit = limit;
  urma_micro_gpu_prepare(p.main, p.g, rank);
}

std::string VerbsWriteSession::load_step(size_t seq, size_t limit, size_t count) {
  auto& p = *impl_;
  using namespace uccl::serial_pair;
  require(p.serial && p.prepared && !p.closed && seq == p.sequence &&
          p.g.slots == count && p.load_limit == limit && p.cpus.size() == 1,
          "invalid verbs load state");
  verify_cpu(p.cpus[0]);
  uccl::gpu_p2p::set_device(0);
  if (!p.send) {
    ++p.sequence;
    return "{\"version\":1,\"receiver_only\":true}";
  }
  auto& s = p.sets[0];
  size_t lanes = p.serial_lanes;
  auto base = reinterpret_cast<uintptr_t>(p.main);
  size_t remote_pad = (p.g.dst_pad + base % 4096 + 4096 - s.remote.addr % 4096) % 4096;
  auto result = load_measure(seq, lanes, limit, count,
      [&](size_t first, size_t n, std::vector<Pair> const& pairs) {
        size_t depth = n / lanes;
        std::array<ibv_sge, 24> sges{};
        std::array<ibv_send_wr, 24> wrs{};
        for (size_t lane = 0; lane < lanes; ++lane) {
          size_t offset = lane * 2 * depth;
          for (size_t j = 0; j < depth; ++j) {
            size_t index = first + j * lanes + lane;
            size_t slot = lane * p.g.window + index / lanes;
            size_t w = offset + j, a = w + depth;
            sges[w].addr = base + (seq % 2) * p.g.region + slot * p.g.stride + p.g.src_pad;
            sges[w].length = p.g.bytes;
            sges[w].lkey = s.mr->lkey;
            wrs[w].wr_id = pairs[index].wid;
            wrs[w].opcode = IBV_WR_RDMA_WRITE;
            wrs[w].wr.rdma.remote_addr = s.remote.addr + 2 * p.g.region + slot * p.g.stride + remote_pad;
            wrs[w].wr.rdma.rkey = s.remote.rkey;
            sges[a].addr = reinterpret_cast<uintptr_t>(&p.returns[index]);
            sges[a].length = sizeof(p.returns[index]);
            sges[a].lkey = p.returns_mr->lkey;
            wrs[a].wr_id = pairs[index].aid;
            wrs[a].opcode = IBV_WR_ATOMIC_FETCH_AND_ADD;
            wrs[a].wr.atomic.remote_addr = s.remote.control + lane * sizeof(uint64_t);
            wrs[a].wr.atomic.rkey = s.remote.control_rkey;
            wrs[a].wr.atomic.compare_add = 32;
          }
          for (size_t k = 0; k < 2 * depth; ++k) {
            size_t i = offset + k;
            wrs[i].sg_list = &sges[i];
            wrs[i].num_sge = 1;
            wrs[i].send_flags = IBV_SEND_SIGNALED;
            wrs[i].next = k + 1 < 2 * depth ? &wrs[i + 1] : nullptr;
          }
          auto stamp = post_tick();
          for (size_t k = 0; k < 2 * depth; ++k)
            posted(wrs[offset + k].wr_id, k < depth, k >= depth, stamp);
          ibv_send_wr* bad = nullptr;
          s.inflight += 2 * depth;
          int rc = ibv_post_send(s.qp[lane], &wrs[offset], &bad);
          post_end(stamp, rc);
        }
      }, [&]() {
        ibv_wc completions[16]{};
        auto poll_seq = poll_begin();
        int n = ibv_poll_cq(s.cq, 16, completions);
        auto stamp = poll_return(poll_seq);
        require(n >= 0 && static_cast<size_t>(n) <= s.inflight, "verbs load poll failed");
        for (int i = 0; i < n; ++i) {
          auto const& wc = completions[i];
          --s.inflight;
          completed(wc.wr_id, stamp, poll_seq, wc.status == IBV_WC_SUCCESS ? 0 : int(wc.status));
        }
      });
  ++p.sequence;
  return result;
}

void VerbsWriteSession::load_validate(size_t seq) {
  auto& p = *impl_;
  ok(p.serial && !p.closed && seq + 1 == p.sequence && p.g.slots == 12,
     "invalid verbs load validation");
  if (p.receive)
    ok(urma_micro_gpu_validate(p.main, p.control, p.g, 1 - p.rank, seq % 2,
                              uint64_t(seq + 1) * p.g.window * 32),
       "verbs load GPU payload/counter validation failed");
}
